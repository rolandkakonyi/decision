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
const CHOICE_LIMIT: usize = 255;
const INSTRUCTIONS: &str = "Select the category that best describes the product's primary purpose. Use the descriptions, not incidental words or included accessories. Choose exactly one offered category.";

struct Args {
    decision: PathBuf,
    provider: String,
    model: String,
    data: Option<PathBuf>,
    sizes: Vec<usize>,
    strategies: Vec<Strategy>,
    rounds: u32,
    warmups: u32,
    warmup_cases: Option<usize>,
    max_calls: u32,
    max_input_tokens: Option<u64>,
    context: String,
}

impl Args {
    fn parse_from(
        arguments: impl IntoIterator<Item = impl Into<std::ffi::OsString> + Clone>,
    ) -> Self {
        let matches = clap::Command::new("hierarchical")
            .about("Compare flat and hierarchical product classification through decision. JSON results and request traces go to stdout; inference is billed by the selected provider.")
            .arg(Arg::new("decision").long("decision").default_value("decision").value_parser(clap::value_parser!(PathBuf)))
            .arg(Arg::new("provider").long("provider").required(true))
            .arg(Arg::new("model").long("model").required(true))
            .arg(Arg::new("data").long("data").value_parser(clap::value_parser!(PathBuf)))
            .arg(Arg::new("sizes").long("sizes").value_delimiter(',').value_parser(clap::value_parser!(u32).range(2..)).action(clap::ArgAction::Append).help("Nested candidate counts, e.g. 128,255,1024; all labeled targets are retained"))
            .arg(Arg::new("strategies").long("strategies").value_delimiter(',').action(clap::ArgAction::Append).value_parser(["flat","greedy","two-branch","two-branch-top-two"]).default_values(["flat","greedy","two-branch"]).help("Strategies to compare; flat is required as the matched baseline"))
            .arg(Arg::new("rounds").long("rounds").default_value("5").value_parser(clap::value_parser!(u32).range(1..)))
            .arg(Arg::new("warmups").long("warmups").default_value("1").value_parser(clap::value_parser!(u32).range(1..)))
            .arg(Arg::new("warmup-cases").long("warmup-cases").value_parser(clap::value_parser!(u32).range(1..)).help("Cases per warmup round; default: entire dataset"))
            .arg(Arg::new("max-calls").long("max-calls").required(true).value_parser(clap::value_parser!(u32).range(1..)).help("Maximum provider attempts including warmups and failures; no retries"))
            .arg(Arg::new("max-input-tokens").long("max-input-tokens").value_parser(clap::value_parser!(u64).range(1..)).help("Stop before another call once reported input tokens reach this total; one call can overshoot"))
            .arg(Arg::new("context").long("context").required(true).help("Host/load notes recorded with results, e.g. laptop idle, Wi-Fi"))
            .get_matches_from(arguments);
        Self {
            decision: matches.get_one::<PathBuf>("decision").unwrap().clone(),
            provider: matches.get_one::<String>("provider").unwrap().clone(),
            model: matches.get_one::<String>("model").unwrap().clone(),
            data: matches.get_one::<PathBuf>("data").cloned(),
            sizes: matches
                .get_many::<u32>("sizes")
                .into_iter()
                .flatten()
                .map(|n| *n as usize)
                .collect(),
            strategies: matches
                .get_many::<String>("strategies")
                .unwrap()
                .map(|name| match name.as_str() {
                    "flat" => Strategy::Flat,
                    "greedy" => Strategy::Greedy,
                    "two-branch" => Strategy::TwoBranch,
                    "two-branch-top-two" => Strategy::TwoBranchTopTwo,
                    _ => unreachable!(),
                })
                .collect(),
            rounds: *matches.get_one::<u32>("rounds").unwrap(),
            warmups: *matches.get_one::<u32>("warmups").unwrap(),
            warmup_cases: matches.get_one::<u32>("warmup-cases").map(|n| *n as usize),
            max_calls: *matches.get_one::<u32>("max-calls").unwrap(),
            max_input_tokens: matches.get_one::<u64>("max-input-tokens").copied(),
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

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Group {
    #[serde(flatten)]
    category: Category,
    children: Vec<Category>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Case {
    id: String,
    text: String,
    expected: String,
}

#[derive(Clone, Deserialize, Serialize)]
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
        if !(2..=CHOICE_LIMIT).contains(&data.groups.len()) || data.cases.is_empty() {
            return Err("Dataset needs at least two groups and one labeled case".into());
        }
        for group in &data.groups {
            if !(2..=CHOICE_LIMIT).contains(&group.children.len()) {
                return Err("Each group needs 2 to 255 leaf categories".into());
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

    fn subset(&self, size: usize) -> Result<Self> {
        let mut selected: HashSet<_> = self.cases.iter().map(|c| c.expected.as_str()).collect();
        for group in &self.groups {
            selected.extend(group.children.iter().take(2).map(|c| c.id.as_str()));
        }
        let total: usize = self.groups.iter().map(|g| g.children.len()).sum();
        if size < selected.len() || size > total {
            return Err(format!(
                "Candidate count must be {} to {total} to retain targets and groups",
                selected.len()
            ));
        }
        let mut index = 0;
        while selected.len() < size {
            for group in &self.groups {
                if selected.len() < size
                    && let Some(child) = group.children.get(index)
                {
                    selected.insert(child.id.as_str());
                }
            }
            index += 1;
        }
        Ok(Self {
            groups: self
                .groups
                .iter()
                .map(|g| Group {
                    category: g.category.clone(),
                    children: g
                        .children
                        .iter()
                        .filter(|c| selected.contains(c.id.as_str()))
                        .cloned()
                        .collect(),
                })
                .collect(),
            cases: self.cases.clone(),
        })
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
    TwoBranchTopTwo,
}
#[cfg(test)]
const STRATEGIES: [Strategy; 3] = [Strategy::Flat, Strategy::Greedy, Strategy::TwoBranch];

fn selected(answer: &Value) -> Result<String> {
    answer["choice"]
        .as_str()
        .map(str::to_owned)
        .ok_or("Missing choice".into())
}

fn ask_one(
    ask: &mut impl FnMut(&[Vec<Category>]) -> Result<Vec<Value>>,
    options: Vec<Category>,
) -> Result<Value> {
    ask(&[options])?.pop().ok_or("Missing answer".into())
}

fn choose_each(
    ask: &mut impl FnMut(&[Vec<Category>]) -> Result<Vec<Value>>,
    menus: Vec<Vec<Category>>,
    keep: usize,
) -> Result<Vec<Category>> {
    let questions: Vec<_> = menus.iter().filter(|m| m.len() > 1).cloned().collect();
    let answers = if questions.is_empty() {
        vec![]
    } else {
        ask(&questions)?
    };
    let mut answers = answers.into_iter();
    menus
        .into_iter()
        .map(|menu| {
            if menu.len() == 1 {
                return Ok(menu);
            }
            let answer = answers.next().ok_or("Missing batch answer")?;
            if keep > 1 {
                return ranked_candidates(&menu, &answer, keep);
            }
            let label = selected(&answer)?;
            menu.into_iter()
                .find(|c| c.id == label)
                .map(|c| vec![c])
                .ok_or("Unknown selected category".into())
        })
        .collect::<Result<Vec<_>>>()
        .map(|menus| menus.into_iter().flatten().collect())
}

fn ranked_candidates(options: &[Category], answer: &Value, keep: usize) -> Result<Vec<Category>> {
    let mut ranked = options
        .iter()
        .map(|category| {
            answer["probabilities"][&category.id]
                .as_f64()
                .map(|probability| (category, probability))
                .ok_or_else(|| "Missing category probability".to_owned())
        })
        .collect::<Result<Vec<_>>>()?;
    ranked.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.id.cmp(&b.0.id)));
    Ok(ranked
        .into_iter()
        .take(keep)
        .map(|(c, _)| c.clone())
        .collect())
}

fn classify(
    data: &Data,
    strategy: Strategy,
    mut ask: impl FnMut(&[Vec<Category>]) -> Result<Vec<Value>>,
) -> Result<String> {
    if strategy == Strategy::Flat {
        let mut options: Vec<_> = data.groups.iter().flat_map(|g| data.leaves(g)).collect();
        if options.len() <= CHOICE_LIMIT {
            return selected(&ask_one(&mut ask, options)?);
        }
        // Fixed hash order keeps chunks independent of semantic taxonomy groups.
        options.sort_by_cached_key(|c| hash(c.id.as_bytes()));
        let winners = choose_each(
            &mut ask,
            options.chunks(CHOICE_LIMIT).map(<[_]>::to_vec).collect(),
            1,
        )?;
        return selected(&ask_one(&mut ask, winners)?);
    }
    let groups: Vec<_> = data.groups.iter().map(|g| g.category.clone()).collect();
    let answer = ask_one(&mut ask, groups.clone())?;
    if strategy == Strategy::Greedy {
        let group_id = selected(&answer)?;
        let group = data
            .groups
            .iter()
            .find(|g| g.category.id == group_id)
            .ok_or("Unknown group")?;
        return selected(&ask_one(&mut ask, data.leaves(group))?);
    }
    let ranked = ranked_candidates(&groups, &answer, 2)?;
    let candidates = choose_each(
        &mut ask,
        ranked
            .into_iter()
            .map(|category| {
                let group = data
                    .groups
                    .iter()
                    .find(|g| g.category.id == category.id)
                    .unwrap();
                data.leaves(group)
            })
            .collect(),
        if strategy == Strategy::TwoBranchTopTwo {
            2
        } else {
            1
        },
    )?;
    // Rerank leaves together: local probabilities from different menus are not comparable.
    selected(&ask_one(&mut ask, candidates)?)
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

struct Budget {
    attempts: u32,
    input_tokens: Option<u64>,
    effective_model: Option<String>,
}

impl Default for Budget {
    fn default() -> Self {
        Self {
            attempts: 0,
            input_tokens: Some(0),
            effective_model: None,
        }
    }
}

fn experiment(
    data: &Data,
    args: &Args,
    budget: &mut Budget,
    mut evaluate: impl FnMut(&Value) -> Result<Value>,
) -> Value {
    let mut runs = Vec::new();
    let initial_attempts = budget.attempts;
    let mut failure = None;
    let groups: serde_json::Map<_, _> = data
        .groups
        .iter()
        .map(|g| (g.category.id.clone(), json!(g.category.description)))
        .collect();
    'experiment: for (phase, rounds) in [("warmup", args.warmups), ("measured", args.rounds)] {
        for round in 0..rounds {
            let case_count = if phase == "warmup" {
                args.warmup_cases.unwrap_or(data.cases.len())
            } else {
                data.cases.len()
            };
            for (case_index, case) in data.cases.iter().take(case_count).enumerate() {
                for offset in 0..args.strategies.len() {
                    let strategy = args.strategies
                        [(round as usize + case_index + offset) % args.strategies.len()];
                    let mut calls = Vec::new();
                    let started = Instant::now();
                    let result = classify(data, strategy, |menus| {
                        if budget.attempts >= args.max_calls {
                            return Err(
                                "Provider attempt limit reached; experiment is incomplete".into()
                            );
                        }
                        if let Some(limit) = args.max_input_tokens {
                            let used = budget.input_tokens.ok_or(
                                "Input usage is unknown; token-limited experiment stopped",
                            )?;
                            if used >= limit {
                                return Err(
                                    "Reported input token limit reached; experiment is incomplete"
                                        .into(),
                                );
                            }
                        }
                        let questions: serde_json::Map<_,_> = menus.iter().enumerate().map(|(index,options)| {
                            let criteria: serde_json::Map<_,_> = options.iter().map(|c| (c.id.clone(),json!(c.description))).collect();
                            (format!("category_{index}"),json!({"type":"choice","instructions":INSTRUCTIONS,"criteria":criteria}))
                        }).collect();
                        let request = json!({"state":{"product":case.text,"group_descriptions":groups},"questions":questions});
                        budget.attempts += 1;
                        let response = evaluate(&request);
                        calls.push(Call {
                            request,
                            response: response.as_ref().ok().cloned(),
                            error: response.as_ref().err().cloned(),
                        });
                        let response = response?;
                        budget.input_tokens = budget.input_tokens.and_then(|used| {
                            used.checked_add(response["usage"]["input_tokens"].as_u64()?)
                        });
                        if args.max_input_tokens.is_some() && budget.input_tokens.is_none() {
                            return Err(
                                "Input usage is unknown; token-limited experiment stopped".into()
                            );
                        }
                        let model = response["model"].as_str().ok_or("Missing response model")?;
                        if budget
                            .effective_model
                            .as_deref()
                            .is_some_and(|previous| previous != model)
                        {
                            return Err("Response model changed; experiment is incomplete".into());
                        }
                        budget.effective_model = Some(model.to_owned());
                        Ok((0..menus.len())
                            .map(|index| response["answers"][format!("category_{index}")].clone())
                            .collect())
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
                if case_count >= 64 && (case_index + 1) % 32 == 0 {
                    eprintln!(
                        "Completed {phase} products {}/{}; {} cumulative attempts, {:?} input tokens",
                        case_index + 1,
                        case_count,
                        budget.attempts,
                        budget.input_tokens
                    );
                }
            }
            eprintln!(
                "Completed {phase} round {} of {rounds}; {} cumulative attempts, {:?} input tokens",
                round + 1,
                budget.attempts,
                budget.input_tokens
            );
        }
    }
    let calls: Vec<_> = runs.iter().flat_map(|r| &r.calls).collect();
    json!({"complete":failure.is_none(),"error":failure,"provider_attempts":budget.attempts-initial_attempts,"effective_model":budget.effective_model,
        "usage_including_warmups":{"input_tokens":token_total(&calls,"input_tokens"),"output_tokens":token_total(&calls,"output_tokens")},
        "summary":if failure.is_none() {summary(&runs,&args.strategies)} else {Value::Null}, "runs":runs})
}

fn token_total(calls: &[&Call], key: &str) -> Option<u64> {
    calls.iter().try_fold(0_u64, |sum, call| {
        sum.checked_add(call.response.as_ref()?["usage"][key].as_u64()?)
    })
}

fn summary(runs: &[Run], strategies: &[Strategy]) -> Value {
    Value::Array(strategies.iter().map(|strategy| {
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
            "questions":calls.iter().map(|call| call.request["questions"].as_object().unwrap().len()).sum::<usize>(),
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
    if !args.strategies.contains(&Strategy::Flat)
        || args
            .strategies
            .iter()
            .enumerate()
            .any(|(index, strategy)| args.strategies[..index].contains(strategy))
    {
        return Err("Strategies must be unique and include flat as the baseline".into());
    }
    let text = match &args.data {
        Some(path) => std::fs::read_to_string(path).map_err(|_| "Cannot read dataset")?,
        None => DATA.to_owned(),
    };
    let data = Data::parse(&text)?;
    let datasets = if args.sizes.is_empty() {
        vec![data]
    } else {
        if args.sizes.windows(2).any(|pair| pair[0] >= pair[1]) {
            return Err("Candidate sizes must be strictly increasing".into());
        }
        args.sizes
            .iter()
            .map(|size| data.subset(*size))
            .collect::<Result<Vec<_>>>()?
    };
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
    let mut budget = Budget::default();
    let mut reports = Vec::new();
    for data in datasets {
        let leaves: usize = data.groups.iter().map(|g| g.children.len()).sum();
        eprintln!("Starting {leaves}-category experiment");
        let mut report = experiment(&data, &args, &mut budget, |request| invoke(&args, request));
        report["metadata"] = json!({"example_version":env!("CARGO_PKG_VERSION"),
        "harness_sha256":hash(include_bytes!("main.rs")),"dataset_sha256":hash(text.as_bytes()),
        "selected_dataset_sha256":hash(&serde_json::to_vec(&data).unwrap()),"leaf_categories":leaves,
        "flat_mode":if leaves>CHOICE_LIMIT {"chunked_batch"} else {"one_shot"},"choice_limit":CHOICE_LIMIT,"independent_questions_batched":true,
        "decision_version":String::from_utf8_lossy(&version.stdout).trim(),
        "provider":args.provider,"model":args.model,"os":std::env::consts::OS,"arch":std::env::consts::ARCH,
        "context":args.context,"started_unix_seconds":started,
        "strategies":args.strategies,"warmup_rounds":args.warmups,"warmup_cases_per_round":args.warmup_cases.unwrap_or(data.cases.len()).min(data.cases.len()),
        "measured_rounds":args.rounds,"max_calls":args.max_calls,"max_input_tokens":args.max_input_tokens});
        report["dataset"] = serde_json::to_value(data).unwrap();
        let complete = report["complete"] == true;
        reports.push(report);
        if !complete {
            break;
        }
    }
    let report = if args.sizes.is_empty() {
        reports.pop().unwrap()
    } else {
        json!({"complete":reports.len()==args.sizes.len() && reports.iter().all(|r| r["complete"]==true),
            "provider_attempts":budget.attempts,"input_tokens_including_warmups":budget.input_tokens,"experiments":reports})
    };
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
        let answers: serde_json::Map<_, _> = request["questions"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(id, q)| {
                let options = q["criteria"].as_object().unwrap();
                let first = options.keys().next().unwrap();
                let probabilities: serde_json::Map<_, _> = options
                    .keys()
                    .map(|key| (key.clone(), json!(1.0 / options.len() as f64)))
                    .collect();
                (
                    id.clone(),
                    json!({"choice":first,"probabilities":probabilities}),
                )
            })
            .collect();
        json!({"model":"fixture","answers":answers,"usage":{"input_tokens":10,"output_tokens":0},"duration_ms":2.0})
    }

    #[test]
    fn multibranch_can_recover_from_wrong_root() {
        let data = data();
        for (strategy, expected, expected_calls) in [
            (Strategy::Flat, "hiking_backpack", 1),
            (Strategy::Greedy, "laptop_backpack", 2),
            (Strategy::TwoBranch, "hiking_backpack", 3),
        ] {
            let mut calls = 0;
            let result = classify(&data, strategy, |menus| {
                calls += 1;
                menus
                    .iter()
                    .map(|options| {
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
                    .collect()
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
        let report = experiment(&data, &args(), &mut Budget::default(), |request| {
            assert!(request["state"]["product"].is_string());
            assert_eq!(
                request["state"]["group_descriptions"]
                    .as_object()
                    .unwrap()
                    .len(),
                4
            );
            assert!(!request.to_string().contains("expected"));
            Ok(response(request))
        });
        assert_eq!(report["complete"], true);
        assert_eq!(report["provider_attempts"], 432);
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
        let report = experiment(&data, &args(), &mut Budget::default(), |_| {
            Err("fixture failure".into())
        });
        assert_eq!(report["complete"], false);
        assert_eq!(report["provider_attempts"], 1);
        assert!(report["summary"].is_null());
        assert_eq!(report["runs"][0]["calls"][0]["error"], "fixture failure");
        let mut args = args();
        args.max_calls = 1;
        let report = experiment(&data, &args, &mut Budget::default(), response_ok);
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
        let report = experiment(&data(), &args(), &mut Budget::default(), |request| {
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

    #[test]
    fn large_flat_tournament_covers_every_leaf_and_reranks_winners() {
        let mut data = data();
        for (group_index, group) in data.groups.iter_mut().enumerate() {
            group.children = (0..64)
                .map(|i| Category {
                    id: format!("g{group_index}-leaf{i}"),
                    description: format!("Leaf {i}"),
                })
                .collect();
        }
        let target = "g3-leaf63";
        let mut seen = HashSet::new();
        let mut calls = 0;
        let prediction = classify(&data, Strategy::Flat, |menus| {
            calls += 1;
            menus
                .iter()
                .map(|options| {
                    assert!((2..=255).contains(&options.len()));
                    seen.extend(options.iter().map(|c| c.id.clone()));
                    let choice = if options.iter().any(|c| c.id == target) {
                        target
                    } else {
                        &options[0].id
                    };
                    Ok(answer(options, choice))
                })
                .collect()
        })
        .unwrap();
        assert_eq!(prediction, target);
        assert_eq!(seen.len(), 256);
        assert_eq!(calls, 2); // The single remaining leaf needs no model call.
    }

    #[test]
    fn subsets_are_nested_and_preserve_labels_and_descriptions() {
        let data = data();
        let smaller = data.subset(16).unwrap();
        let larger = data.subset(20).unwrap();
        let ids = |data: &Data| {
            data.groups
                .iter()
                .flat_map(|g| &g.children)
                .map(|c| c.id.clone())
                .collect::<HashSet<_>>()
        };
        assert_eq!(ids(&smaller).len(), 16);
        assert!(ids(&smaller).is_subset(&ids(&larger)));
        assert!(
            data.cases
                .iter()
                .all(|case| ids(&smaller).contains(&case.expected))
        );
        for group in &smaller.groups {
            assert!(group.children.len() >= 2);
            for leaf in &group.children {
                assert!(
                    data.groups
                        .iter()
                        .flat_map(|g| &g.children)
                        .any(|original| original.id == leaf.id
                            && original.description == leaf.description)
                );
            }
        }
        assert!(data.subset(2).is_err());
        assert!(data.subset(25).is_err());
    }

    #[test]
    fn budgets_apply_across_scales_and_stop_on_unknown_usage() {
        let data = data();
        let mut args = args();
        args.max_input_tokens = Some(15);
        let mut budget = Budget::default();
        let first = experiment(&data, &args, &mut budget, response_ok);
        assert_eq!(first["provider_attempts"], 2);
        assert_eq!(budget.input_tokens, Some(20));
        let second = experiment(&data, &args, &mut budget, |_| {
            panic!("Budget must stop calls")
        });
        assert_eq!(second["provider_attempts"], 0);
        assert_eq!(second["complete"], false);
        let report = experiment(&data, &args, &mut Budget::default(), |request| {
            let mut response = response(request);
            response["usage"]["input_tokens"] = Value::Null;
            Ok(response)
        });
        assert_eq!(report["provider_attempts"], 1);
        assert_eq!(report["complete"], false);
    }

    #[test]
    fn shopify_scales_use_identical_cases_and_batched_flat_chunks() {
        let data = Data::parse(include_str!("shopify.json")).unwrap();
        let mut previous = HashSet::new();
        for size in [128, 255, 1024] {
            let subset = data.subset(size).unwrap();
            let ids: HashSet<_> = subset
                .groups
                .iter()
                .flat_map(|g| &g.children)
                .map(|c| c.id.clone())
                .collect();
            assert_eq!(ids.len(), size);
            assert!(previous.is_subset(&ids));
            previous = ids;
            assert_eq!(
                serde_json::to_value(&subset.cases).unwrap(),
                serde_json::to_value(&data.cases).unwrap()
            );
            let mut calls = 0;
            let mut batches = Vec::new();
            classify(&subset, Strategy::Flat, |menus| {
                calls += 1;
                batches.push(menus.len());
                assert!(menus.iter().all(|m| (2..=255).contains(&m.len())));
                Ok(menus.iter().map(|m| answer(m, &m[0].id)).collect())
            })
            .unwrap();
            assert_eq!(calls, if size > 255 { 2 } else { 1 });
            assert_eq!(batches, if size > 255 { vec![5, 1] } else { vec![1] });
        }
    }

    #[test]
    fn top_two_preserves_second_ranked_leaf_for_reranking() {
        let data = Data::parse(r#"{"groups":[
            {"id":"a","description":"A","children":[{"id":"a1","description":"A1"},{"id":"a2","description":"A2"},{"id":"a3","description":"A3"}]},
            {"id":"b","description":"B","children":[{"id":"b1","description":"B1"},{"id":"b2","description":"B2"},{"id":"b3","description":"B3"}]}
        ],"cases":[{"id":"case","text":"product","expected":"a2"}]}"#).unwrap();
        for (strategy, expected, final_size) in [
            (Strategy::Flat, "a2", 6),
            (Strategy::TwoBranch, "a1", 2),
            (Strategy::TwoBranchTopTwo, "a2", 4),
        ] {
            let mut calls = 0;
            let result = classify(&data, strategy, |menus| {
                calls += 1;
                if calls == 2 {
                    assert_eq!(menus.len(), 2);
                    assert!(menus.iter().all(|menu| menu.len() == 3));
                    return Ok(menus
                        .iter()
                        .map(|menu| {
                            let mut result = answer(menu, &menu[0].id);
                            result["probabilities"][&menu[0].id] = json!(0.5);
                            result["probabilities"][&menu[1].id] = json!(0.3);
                            result["probabilities"][&menu[2].id] = json!(0.2);
                            result
                        })
                        .collect());
                }
                let menu = &menus[0];
                if strategy == Strategy::Flat || calls == 3 {
                    assert_eq!(menu.len(), final_size);
                    assert!(
                        !menu.iter().any(|c| c.id == "a3" || c.id == "b3")
                            || strategy == Strategy::Flat
                    );
                }
                let chosen = if menu.iter().any(|c| c.id == "a2") {
                    "a2"
                } else {
                    &menu[0].id
                };
                Ok(vec![answer(menu, chosen)])
            })
            .unwrap();
            assert_eq!(result, expected);
            assert_eq!(calls, if strategy == Strategy::Flat { 1 } else { 3 });
        }
    }

    #[test]
    fn probability_ranking_breaks_ties_by_id_and_caps_candidates() {
        let options = vec![
            Category {
                id: "b".into(),
                description: "B".into(),
            },
            Category {
                id: "a".into(),
                description: "A".into(),
            },
        ];
        let tied = json!({"probabilities":{"a":0.5,"b":0.5}});
        let ranked = ranked_candidates(&options, &tied, 3).unwrap();
        assert_eq!(
            ranked.iter().map(|c| c.id.as_str()).collect::<Vec<_>>(),
            vec!["a", "b"]
        );
        assert!(ranked_candidates(&options, &json!({}), 2).is_err());
    }

    #[test]
    fn selected_strategies_and_small_warmup_do_not_spend_on_greedy() {
        let args = Args::parse_from([
            "hierarchical",
            "--provider",
            "gemini",
            "--model",
            "fixture",
            "--max-calls",
            "1000",
            "--context",
            "local fixture",
            "--strategies",
            "flat,two-branch-top-two",
            "--rounds",
            "1",
            "--warmup-cases",
            "2",
        ]);
        assert_eq!(
            args.strategies,
            vec![Strategy::Flat, Strategy::TwoBranchTopTwo]
        );
        let report = experiment(&data(), &args, &mut Budget::default(), response_ok);
        assert_eq!(report["complete"], true);
        assert_eq!(report["provider_attempts"], 56);
        assert_eq!(report["runs"].as_array().unwrap().len(), 28);
        assert_eq!(
            report["runs"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|r| r["phase"] == "warmup")
                .count(),
            4
        );
        assert_eq!(report["summary"].as_array().unwrap().len(), 2);
        assert!(
            report["runs"]
                .as_array()
                .unwrap()
                .iter()
                .all(|r| r["strategy"] != "greedy")
        );
        assert_eq!(report["summary"][0]["classifications"], 12);
        assert_eq!(report["summary"][1]["classifications"], 12);
    }
}
