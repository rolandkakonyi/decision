use clap::Arg;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    io::Write,
    path::PathBuf,
    process::{Command, Stdio},
    time::{Instant, SystemTime, UNIX_EPOCH},
};

type Result<T> = std::result::Result<T, String>;
const DATA: &str = include_str!("data.json");
const INSTRUCTIONS: &str = "Select the category that best describes the product's primary purpose. Use the descriptions, not incidental words or included accessories. Choose exactly one offered category.";

struct Args {
    decision: PathBuf,
    provider: String,
    model: String,
    data: Option<PathBuf>,
    rounds: u32,
    warmups: u32,
    max_calls: u32,
    context: String,
}

impl Args {
    fn parse_from(
        arguments: impl IntoIterator<Item = impl Into<std::ffi::OsString> + Clone>,
    ) -> Self {
        let matches = clap::Command::new("hierarchical")
            .about("Compare flat, greedy, and two-branch product classification through decision. JSON results and request traces go to stdout; inference is billed by the selected provider.")
            .arg(Arg::new("decision").long("decision").default_value("decision").value_parser(clap::value_parser!(PathBuf)))
            .arg(Arg::new("provider").long("provider").required(true))
            .arg(Arg::new("model").long("model").required(true))
            .arg(Arg::new("data").long("data").value_parser(clap::value_parser!(PathBuf)))
            .arg(Arg::new("rounds").long("rounds").default_value("5").value_parser(clap::value_parser!(u32).range(5..)))
            .arg(Arg::new("warmups").long("warmups").default_value("1").value_parser(clap::value_parser!(u32).range(1..)))
            .arg(Arg::new("max-calls").long("max-calls").required(true).value_parser(clap::value_parser!(u32).range(1..)).help("Maximum provider attempts including warmups and failures; no retries"))
            .arg(Arg::new("context").long("context").required(true).help("Host/load notes recorded with results, e.g. laptop idle, Wi-Fi"))
            .get_matches_from(arguments);
        Self {
            decision: matches.get_one::<PathBuf>("decision").unwrap().clone(),
            provider: matches.get_one::<String>("provider").unwrap().clone(),
            model: matches.get_one::<String>("model").unwrap().clone(),
            data: matches.get_one::<PathBuf>("data").cloned(),
            rounds: *matches.get_one::<u32>("rounds").unwrap(),
            warmups: *matches.get_one::<u32>("warmups").unwrap(),
            max_calls: *matches.get_one::<u32>("max-calls").unwrap(),
            context: matches.get_one::<String>("context").unwrap().clone(),
        }
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Category {
    id: String,
    description: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Group {
    #[serde(flatten)]
    category: Category,
    children: Vec<Category>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Case {
    id: String,
    text: String,
    expected: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Data {
    groups: Vec<Group>,
    cases: Vec<Case>,
}

impl Data {
    fn parse(text: &str) -> Result<Self> {
        let data: Self = serde_json::from_str(text).map_err(|e| format!("Invalid dataset: {e}"))?;
        let mut ids = HashSet::new();
        let mut leaves = HashSet::new();
        if data.groups.len() < 2 || data.cases.is_empty() {
            return Err("Dataset needs at least two groups and one labeled case".into());
        }
        for group in &data.groups {
            if group.children.len() < 2 {
                return Err("Each group needs at least two leaf categories".into());
            }
            for category in std::iter::once(&group.category).chain(&group.children) {
                if category.id.trim().is_empty()
                    || category.description.trim().is_empty()
                    || !ids.insert(&category.id)
                {
                    return Err("Category IDs must be unique and descriptions nonempty".into());
                }
            }
            leaves.extend(group.children.iter().map(|c| &c.id));
        }
        let mut cases = HashSet::new();
        for case in &data.cases {
            if case.id.trim().is_empty()
                || case.text.trim().is_empty()
                || !cases.insert(&case.id)
                || !leaves.contains(&case.expected)
            {
                return Err("Cases need unique IDs, nonempty text, and an expected leaf ID".into());
            }
        }
        Ok(data)
    }

    fn leaves(&self, group: &Group) -> Vec<Category> {
        group
            .children
            .iter()
            .map(|c| Category {
                id: c.id.clone(),
                description: format!("{} / {}", group.category.id, c.description),
            })
            .collect()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Strategy {
    Flat,
    Greedy,
    TwoBranch,
}
const STRATEGIES: [Strategy; 3] = [Strategy::Flat, Strategy::Greedy, Strategy::TwoBranch];

fn selected(answer: &Value) -> Result<String> {
    answer["choice"]
        .as_str()
        .map(str::to_owned)
        .ok_or("Missing choice".into())
}

fn classify(
    data: &Data,
    strategy: Strategy,
    mut ask: impl FnMut(&[Category]) -> Result<Value>,
) -> Result<String> {
    if strategy == Strategy::Flat {
        return selected(&ask(&data
            .groups
            .iter()
            .flat_map(|g| data.leaves(g))
            .collect::<Vec<_>>())?);
    }
    let groups: Vec<_> = data.groups.iter().map(|g| g.category.clone()).collect();
    let answer = ask(&groups)?;
    if strategy == Strategy::Greedy {
        let group_id = selected(&answer)?;
        let group = data
            .groups
            .iter()
            .find(|g| g.category.id == group_id)
            .ok_or("Unknown group")?;
        return selected(&ask(&data.leaves(group))?);
    }
    let mut ranked = Vec::new();
    for group in &data.groups {
        let probability = answer["probabilities"][&group.category.id]
            .as_f64()
            .ok_or("Missing group probability")?;
        ranked.push((group, probability));
    }
    ranked.sort_by(|a, b| {
        b.1.total_cmp(&a.1)
            .then(a.0.category.id.cmp(&b.0.category.id))
    });
    let mut candidates = Vec::new();
    for (group, _) in ranked.into_iter().take(2) {
        let options = data.leaves(group);
        let label = selected(&ask(&options)?)?;
        candidates.push(
            options
                .into_iter()
                .find(|c| c.id == label)
                .ok_or("Unknown leaf")?,
        );
    }
    // Rerank leaves together: local probabilities from different menus are not comparable.
    selected(&ask(&candidates)?)
}

#[derive(Serialize)]
struct Call {
    request: Value,
    response: Option<Value>,
    error: Option<String>,
}

#[derive(Serialize)]
struct Run {
    sequence: usize,
    phase: &'static str,
    round: u32,
    case_id: String,
    strategy: Strategy,
    expected: String,
    predicted: Option<String>,
    prediction_sha256: Option<String>,
    correct: Option<bool>,
    wall_ms: f64,
    calls: Vec<Call>,
    error: Option<String>,
}

fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn experiment(
    data: &Data,
    args: &Args,
    mut evaluate: impl FnMut(&Value) -> Result<Value>,
) -> Value {
    let mut runs = Vec::new();
    let mut attempts = 0;
    let mut failure = None;
    let mut effective_model = None;
    let groups: serde_json::Map<_, _> = data
        .groups
        .iter()
        .map(|g| (g.category.id.clone(), json!(g.category.description)))
        .collect();
    'experiment: for (phase, rounds) in [("warmup", args.warmups), ("measured", args.rounds)] {
        for round in 0..rounds {
            for (case_index, case) in data.cases.iter().enumerate() {
                for offset in 0..STRATEGIES.len() {
                    let strategy =
                        STRATEGIES[(round as usize + case_index + offset) % STRATEGIES.len()];
                    let mut calls = Vec::new();
                    let started = Instant::now();
                    let result = classify(data, strategy, |options| {
                        if attempts >= args.max_calls {
                            return Err(
                                "Provider attempt limit reached; experiment is incomplete".into()
                            );
                        }
                        let criteria: serde_json::Map<_, _> = options
                            .iter()
                            .map(|c| (c.id.clone(), json!(c.description)))
                            .collect();
                        let request = json!({"state":{"product":case.text,"group_descriptions":groups},"questions":{"category":{"type":"choice","instructions":INSTRUCTIONS,"criteria":criteria}}});
                        attempts += 1;
                        let response = evaluate(&request);
                        calls.push(Call {
                            request,
                            response: response.as_ref().ok().cloned(),
                            error: response.as_ref().err().cloned(),
                        });
                        let response = response?;
                        let model = response["model"].as_str().ok_or("Missing response model")?;
                        if effective_model
                            .as_deref()
                            .is_some_and(|previous| previous != model)
                        {
                            return Err("Response model changed; experiment is incomplete".into());
                        }
                        effective_model = Some(model.to_owned());
                        Ok(response["answers"]["category"].clone())
                    });
                    let wall_ms = started.elapsed().as_secs_f64() * 1000.0;
                    let predicted = result.as_ref().ok().cloned();
                    runs.push(Run {
                        sequence: runs.len() + 1,
                        phase,
                        round: round + 1,
                        case_id: case.id.clone(),
                        strategy,
                        expected: case.expected.clone(),
                        prediction_sha256: predicted
                            .as_ref()
                            .map(|s| hash(&serde_json::to_vec(s).unwrap())),
                        correct: predicted.as_ref().map(|s| s == &case.expected),
                        predicted,
                        wall_ms,
                        calls,
                        error: result.as_ref().err().cloned(),
                    });
                    if let Err(error) = result {
                        failure = Some(error);
                        break 'experiment;
                    }
                }
            }
            eprintln!("Completed {phase} round {} of {rounds}", round + 1);
        }
    }
    let calls: Vec<_> = runs.iter().flat_map(|r| &r.calls).collect();
    json!({"complete":failure.is_none(),"error":failure,"provider_attempts":attempts,"effective_model":effective_model,
        "usage_including_warmups":{"input_tokens":token_total(&calls,"input_tokens"),"output_tokens":token_total(&calls,"output_tokens")},
        "summary":if failure.is_none() {summary(&runs)} else {Value::Null}, "runs":runs})
}

fn token_total(calls: &[&Call], key: &str) -> Option<u64> {
    calls.iter().try_fold(0_u64, |sum, call| {
        sum.checked_add(call.response.as_ref()?["usage"][key].as_u64()?)
    })
}

fn summary(runs: &[Run]) -> Value {
    Value::Array(STRATEGIES.iter().map(|strategy| {
        let measured: Vec<_> = runs.iter().filter(|r| r.phase == "measured" && r.strategy == *strategy).collect();
        let calls: Vec<_> = measured.iter().flat_map(|r| &r.calls).collect();
        let mut times: Vec<_> = measured.iter().map(|r| r.wall_ms).collect();
        times.sort_by(f64::total_cmp);
        let correct = measured.iter().filter(|r| r.correct == Some(true)).count();
        let provider_ms: Option<f64> = calls.iter().map(|c| c.response.as_ref()?["duration_ms"].as_f64()).sum();
        let paired: Vec<_> = measured.iter().map(|r| {
            let flat = runs.iter().find(|f| f.phase == "measured" && f.round == r.round && f.case_id == r.case_id && f.strategy == Strategy::Flat).unwrap();
            json!({"case_id":r.case_id,"round":r.round,"same_prediction":r.predicted == flat.predicted,
                "correctness_delta":i32::from(r.correct == Some(true)) - i32::from(flat.correct == Some(true)),
                "wall_ms_delta":r.wall_ms - flat.wall_ms})
        }).collect();
        json!({"strategy":strategy,"classifications":measured.len(),"correct":correct,
            "accuracy":correct as f64 / measured.len() as f64,"requests":calls.len(),
            "input_tokens":token_total(&calls,"input_tokens"),"output_tokens":token_total(&calls,"output_tokens"),
            "provider_ms_total":provider_ms,"wall_ms_total":times.iter().sum::<f64>(),
            "wall_ms_median":times[(times.len()-1)/2],"wall_ms_p95":times[(times.len()*95).div_ceil(100)-1],
            "paired_with_flat":paired})
    }).collect())
}

fn invoke(args: &Args, request: &Value) -> Result<Value> {
    let mut child = Command::new(&args.decision)
        .args([
            "run",
            "-",
            "--json",
            "--provider",
            &args.provider,
            "--model",
            &args.model,
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|_| "Cannot start decision; check --decision".to_string())?;
    let write = child
        .stdin
        .take()
        .unwrap()
        .write_all(&serde_json::to_vec(request).unwrap());
    let output = child
        .wait_with_output()
        .map_err(|_| "Cannot wait for decision".to_string())?;
    if !output.status.success() {
        return Err(format!(
            "decision failed with exit code {:?}; experiment stopped without retry",
            output.status.code()
        ));
    }
    write.map_err(|_| "Cannot write request to decision".to_string())?;
    serde_json::from_slice(&output.stdout).map_err(|_| "decision returned invalid JSON".into())
}

fn run() -> Result<bool> {
    let args = Args::parse_from(std::env::args_os());
    let text = match &args.data {
        Some(path) => std::fs::read_to_string(path).map_err(|_| "Cannot read dataset")?,
        None => DATA.to_owned(),
    };
    let data = Data::parse(&text)?;
    let version = Command::new(&args.decision)
        .arg("--version")
        .output()
        .map_err(|_| "Cannot read decision version; check --decision")?;
    if !version.status.success() {
        return Err("Cannot read decision version".into());
    }
    let started = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "Invalid system clock")?
        .as_secs();
    let mut report = experiment(&data, &args, |request| invoke(&args, request));
    report["metadata"] = json!({"example_version":env!("CARGO_PKG_VERSION"),
        "harness_sha256":hash(include_bytes!("main.rs")),"dataset_sha256":hash(text.as_bytes()),
        "decision_version":String::from_utf8_lossy(&version.stdout).trim(),
        "provider":args.provider,"model":args.model,"os":std::env::consts::OS,"arch":std::env::consts::ARCH,
        "context":args.context,"started_unix_seconds":started,
        "warmup_rounds":args.warmups,"measured_rounds":args.rounds,"max_calls":args.max_calls});
    report["dataset"] = serde_json::to_value(data).unwrap();
    println!("{}", serde_json::to_string_pretty(&report).unwrap());
    Ok(report["complete"] == true)
}

fn main() {
    match run() {
        Ok(true) => (),
        Ok(false) => std::process::exit(1),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn data() -> Data {
        Data::parse(DATA).unwrap()
    }
    fn args() -> Args {
        Args::parse_from([
            "hierarchical",
            "--provider",
            "gemini",
            "--model",
            "fixture",
            "--max-calls",
            "1000",
            "--context",
            "local fixture",
        ])
    }
    fn answer(options: &[Category], selected: &str) -> Value {
        let probabilities: serde_json::Map<_, _> = options
            .iter()
            .map(|c| {
                (
                    c.id.clone(),
                    json!(if c.id == selected {
                        0.6
                    } else {
                        0.4 / (options.len() - 1) as f64
                    }),
                )
            })
            .collect();
        json!({"choice":selected,"probabilities":probabilities})
    }
    fn response(request: &Value) -> Value {
        let first = request["questions"]["category"]["criteria"]
            .as_object()
            .unwrap()
            .keys()
            .next()
            .unwrap();
        json!({"model":"fixture","answers":{"category":{"choice":first}},"usage":{"input_tokens":10,"output_tokens":0},"duration_ms":2.0})
    }

    #[test]
    fn multibranch_can_recover_from_wrong_root() {
        let data = data();
        for (strategy, expected, expected_calls) in [
            (Strategy::Flat, "hiking_backpack", 1),
            (Strategy::Greedy, "laptop_backpack", 2),
            (Strategy::TwoBranch, "hiking_backpack", 4),
        ] {
            let mut calls = 0;
            let result = classify(&data, strategy, |options| {
                calls += 1;
                let contains = |id| options.iter().any(|c| c.id == id);
                let selected = if contains("everyday_bags") {
                    "everyday_bags"
                } else if contains("hiking_backpack") {
                    "hiking_backpack"
                } else {
                    "laptop_backpack"
                };
                let mut result = answer(options, selected);
                if contains("everyday_bags") {
                    result["probabilities"]["outdoor_equipment"] = json!(0.35);
                    result["probabilities"]["electronics"] = json!(0.03);
                    result["probabilities"]["home_and_kitchen"] = json!(0.02);
                }
                Ok(result)
            })
            .unwrap();
            assert_eq!(result, expected);
            assert_eq!(calls, expected_calls);
        }
    }

    #[test]
    fn leaf_descriptions_match_across_strategies() {
        let data = data();
        let flat: Vec<_> = data.groups.iter().flat_map(|g| data.leaves(g)).collect();
        assert_eq!(flat.len(), 24);
        for group in &data.groups {
            for leaf in data.leaves(group) {
                assert_eq!(
                    flat.iter().find(|c| c.id == leaf.id).unwrap().description,
                    leaf.description
                );
                assert!(leaf.description.starts_with(&group.category.id));
            }
        }
    }

    #[test]
    fn matched_runs_rotate_order_and_keep_warmups_separate() {
        let data = data();
        let report = experiment(&data, &args(), |request| {
            assert!(request["state"]["product"].is_string());
            assert_eq!(
                request["state"]["group_descriptions"]
                    .as_object()
                    .unwrap()
                    .len(),
                4
            );
            assert!(!request.to_string().contains("expected"));
            let options = request["questions"]["category"]["criteria"]
                .as_object()
                .unwrap();
            let mut result = response(request);
            result["answers"]["category"]["probabilities"] = Value::Object(
                options
                    .keys()
                    .map(|k| (k.clone(), json!(1.0 / options.len() as f64)))
                    .collect(),
            );
            Ok(result)
        });
        assert_eq!(report["complete"], true);
        assert_eq!(report["provider_attempts"], 504);
        let runs = report["runs"].as_array().unwrap();
        assert_eq!(runs.len(), 12 * 3 * 6);
        assert_eq!(runs.iter().filter(|r| r["phase"] == "warmup").count(), 36);
        let measured: Vec<_> = runs.iter().filter(|r| r["phase"] == "measured").collect();
        for round in 0..5 {
            assert_eq!(
                measured[round * 36]["strategy"],
                serde_json::to_value(STRATEGIES[round % 3]).unwrap()
            );
        }
        for row in report["summary"].as_array().unwrap() {
            assert_eq!(row["classifications"], 60);
            assert_eq!(row["output_tokens"], 0);
            assert_eq!(row["paired_with_flat"].as_array().unwrap().len(), 60);
        }
    }

    #[test]
    fn failures_and_call_limit_leave_incomplete_evidence() {
        let data = data();
        let report = experiment(&data, &args(), |_| Err("fixture failure".into()));
        assert_eq!(report["complete"], false);
        assert_eq!(report["provider_attempts"], 1);
        assert!(report["summary"].is_null());
        assert_eq!(report["runs"][0]["calls"][0]["error"], "fixture failure");
        let mut args = args();
        args.max_calls = 1;
        let report = experiment(&data, &args, response_ok);
        assert_eq!(report["provider_attempts"], 1);
        assert_eq!(report["runs"].as_array().unwrap().len(), 2);
        assert_eq!(report["complete"], false);
    }
    fn response_ok(request: &Value) -> Result<Value> {
        Ok(response(request))
    }

    #[test]
    fn unknown_usage_stays_unknown_and_changed_model_stops() {
        let call = Call {
            request: json!({}),
            response: Some(json!({"usage":{"input_tokens":null,"output_tokens":0}})),
            error: None,
        };
        assert_eq!(token_total(&[&call], "input_tokens"), None);
        assert_eq!(token_total(&[&call], "output_tokens"), Some(0));
        let mut calls = 0;
        let report = experiment(&data(), &args(), |request| {
            calls += 1;
            let mut result = response(request);
            if calls > 1 {
                result["model"] = json!("different");
            }
            Ok(result)
        });
        assert_eq!(report["complete"], false);
        assert_eq!(report["provider_attempts"], 2);
    }

    #[test]
    fn reject_duplicate_categories_and_invalid_ground_truth() {
        let mut value: Value = serde_json::from_str(DATA).unwrap();
        value["groups"][1]["children"][0]["id"] = value["groups"][0]["children"][0]["id"].clone();
        assert!(Data::parse(&value.to_string()).is_err());
        let mut value: Value = serde_json::from_str(DATA).unwrap();
        value["cases"][0]["expected"] = json!("everyday_bags");
        assert!(Data::parse(&value.to_string()).is_err());
    }
}
