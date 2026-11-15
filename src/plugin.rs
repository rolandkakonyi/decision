//! Deterministic Codex-plugin packaging, separate from runtime provider packages.

use crate::{Error, Result, skill};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    ffi::OsStr,
    fs,
    path::{Path, PathBuf},
};

const MARKETPLACE: &str = "decision-local";
const PACKAGE: &str = "plugins/decision";
const MANIFEST: &str = "plugins/decision/.codex-plugin/plugin.json";
const MCP: &str = "plugins/decision/.mcp.json";
const CATALOG: &str = ".agents/plugins/marketplace.json";
const REPOSITORY: &str = "https://github.com/rolandkakonyi/decision";
pub(crate) const GUIDE: &str = include_str!("templates/plugin-guide.md");
const ENV_VARS: &[&str] = &[
    "DECISION_PROVIDER",
    "DECISION_BASE_URL",
    "DECISION_API_KEY_ENV",
    "DECISION_API_KEY",
    "DECISION_MODEL",
    "DECISION_ACCOUNT_ID",
    "TYPESAFE_BASE_URL",
    "TYPESAFE_API_KEY",
    "TYPESAFEAI_API_KEY",
    "TYPESAFE_DEFAULT_MODEL",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "GEMINI_BASE_URL",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "CLOUDFLARE_BASE_URL",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_AUTH_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
];

pub fn export(
    output: &Path,
    integration: &str,
    command: &str,
    env_vars: &[String],
    check: bool,
) -> Result<()> {
    let files = plugin_files(
        skill::files()?,
        env!("CARGO_PKG_VERSION"),
        integration,
        command,
        env_vars,
    )?;
    let root = output_root(output)?;
    export_files(&files, &root, check)
}

fn output_root(output: &Path) -> Result<PathBuf> {
    let expanded = if let Ok(relative) = output.strip_prefix("~") {
        #[cfg(windows)]
        let home = std::env::var_os("USERPROFILE");
        #[cfg(not(windows))]
        let home = std::env::var_os("HOME");
        PathBuf::from(home.ok_or_else(|| Error::input("Cannot expand plugin output home"))?)
            .join(relative)
    } else {
        output.to_path_buf()
    };
    std::path::absolute(expanded).map_err(|_| Error::input("Cannot resolve plugin output"))
}

fn environment_name(name: &str) -> bool {
    let mut bytes = name.bytes();
    bytes
        .next()
        .is_some_and(|byte| byte == b'_' || byte.is_ascii_alphabetic())
        && bytes.all(|byte| byte == b'_' || byte.is_ascii_alphanumeric())
}

fn plugin_files(
    skill: skill::Files,
    version: &str,
    integration: &str,
    command: &str,
    env_vars: &[String],
) -> Result<skill::Files> {
    let note = match integration {
        "cli" => include_str!("templates/integration-cli.md"),
        "mcp" => include_str!("templates/integration-mcp.md"),
        _ => return Err(Error::input("Plugin integration must be cli or mcp")),
    };
    if integration == "cli" && (command != "decision" || !env_vars.is_empty()) {
        return Err(Error::input(
            "--mcp-command and --env-var apply only to MCP integration",
        ));
    }
    if command.trim().is_empty() || command.contains(['\r', '\n', '\0']) {
        return Err(Error::input(
            "MCP command must be an executable name or absolute path, without line breaks",
        ));
    }
    if !Path::new(command).is_absolute()
        && (Path::new(command).file_name() != Some(OsStr::new(command))
            || command.chars().any(char::is_whitespace))
    {
        return Err(Error::input(
            "Use an MCP executable name or absolute path, not a relative path or shell command",
        ));
    }
    if env_vars.iter().any(|name| !environment_name(name)) {
        return Err(Error::input(
            "--env-var requires an environment variable name, never NAME=value",
        ));
    }
    let mut files: skill::Files = skill
        .into_iter()
        .map(|(name, content)| {
            (
                Path::new(PACKAGE).join("skills/decision").join(name),
                content,
            )
        })
        .collect();
    let entrypoint = Path::new(PACKAGE).join("skills/decision/SKILL.md");
    let content = files
        .get_mut(&entrypoint)
        .ok_or_else(|| Error::execution("Generated skill entrypoint is missing"))?;
    let mut text = String::from_utf8(content.clone())
        .map_err(|_| Error::execution("Cannot encode generated skill"))?
        .replacen(
            "# Decision\n",
            &format!("# Decision\n\n## Plugin integration\n\n{note}"),
            1,
        );
    if integration == "cli" {
        text = text.replace(
            "Prefer this tool when available for structured code-mode composition.",
            "This CLI plugin uses command invocations for structured code-mode composition.",
        );
    }
    *content = text.into_bytes();
    let mut manifest = json!({
        "name": "decision", "description": "Typed judgments with Jev, OpenAI/Gemini Decisions, or Cloudflare Clef.",
        "author": {"name": "Roland Kakonyi"}, "repository": REPOSITORY,
        "skills": "./skills/decision", "requires_local_executor": true,
        "interface": {"displayName": "Decision", "shortDescription": "Typed judgments over text and images"}
    });
    if integration == "mcp" {
        manifest["mcpServers"] = "./.mcp.json".into();
        let names: BTreeSet<&str> = ENV_VARS
            .iter()
            .copied()
            .chain(env_vars.iter().map(String::as_str))
            .collect();
        files.insert(
            PathBuf::from(MCP),
            skill::encoded(&json!({"mcpServers": {"decision": {
                "command": command, "args": ["mcp"], "env_vars": names
            }}}))?,
        );
    }
    // Match the existing content-version contract, with POSIX relative names on
    // every host so Windows path separators do not change the cache version.
    let mut digest = Sha256::new();
    digest.update(skill::encoded(&manifest)?);
    let mut content: Vec<_> = files
        .iter()
        .map(|(path, bytes)| {
            let name = path
                .iter()
                .map(|part| part.to_string_lossy())
                .collect::<Vec<_>>()
                .join("/");
            (name, bytes)
        })
        .collect();
    content.sort_by(|a, b| a.0.cmp(&b.0));
    for (name, bytes) in content {
        digest.update(name.as_bytes());
        digest.update(b"\0");
        digest.update(bytes);
    }
    let hash: String = digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    manifest["version"] = format!("{version}+{}", &hash[..16]).into();
    files.insert(PathBuf::from(MANIFEST), skill::encoded(&manifest)?);
    files.insert(PathBuf::from(CATALOG), skill::encoded(&json!({
        "name": MARKETPLACE, "interface": {"displayName": "Decision Local"},
        "plugins": [{"name": "decision", "source": {"source": "local", "path": "./plugins/decision"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]
    }))?);
    files.insert(
        PathBuf::from("README.md"),
        format!("<!-- Generated by decision {version}; integration: {integration}. -->\n\n{GUIDE}")
            .into_bytes(),
    );
    Ok(files)
}

fn export_files(files: &skill::Files, root: &Path, check: bool) -> Result<()> {
    let existing = skill::existing_files(root)?;
    let expected: BTreeSet<_> = files.keys().cloned().collect();
    if check {
        if existing != expected
            || files
                .iter()
                .any(|(name, content)| fs::read(root.join(name)).ok().as_ref() != Some(content))
        {
            return Err(Error::input(
                "Generated plugin is stale; regenerate with the same integration options",
            ));
        }
        return Ok(());
    }
    let unmanaged = || {
        Error::input(
            "Plugin output contains unmanaged files; choose an empty directory or a previous generated export",
        )
    };
    if !existing.is_empty() {
        let read_json = |name: &str| -> Option<Value> {
            serde_json::from_slice(&fs::read(root.join(name)).ok()?).ok()
        };
        let owned = read_json(MANIFEST).is_some_and(|manifest| {
            manifest["name"] == "decision" && manifest["repository"] == REPOSITORY
        }) && read_json(CATALOG).is_some_and(|catalog| catalog["name"] == MARKETPLACE)
            && fs::read_to_string(root.join("README.md"))
                .is_ok_and(|readme| readme.starts_with("<!-- Generated by decision "));
        if !owned {
            return Err(unmanaged());
        }
    }
    let stale_mcp = existing.contains(Path::new(MCP))
        && existing.contains(Path::new(MANIFEST))
        && existing.contains(Path::new(CATALOG));
    if existing
        .difference(&expected)
        .any(|name| !(stale_mcp && name == Path::new(MCP)))
    {
        return Err(unmanaged());
    }
    skill::write_files(root, files)?;
    if stale_mcp && !expected.contains(Path::new(MCP)) {
        fs::remove_file(root.join(MCP))
            .map_err(|_| Error::input("Cannot remove stale MCP declaration"))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn files(integration: &str, command: &str, names: &[String]) -> skill::Files {
        plugin_files(
            skill::files().unwrap(),
            "0.10.0",
            integration,
            command,
            names,
        )
        .unwrap()
    }
    fn json_file(files: &skill::Files, name: &str) -> Value {
        serde_json::from_slice(&files[Path::new(name)]).unwrap()
    }
    fn version(files: &skill::Files) -> String {
        json_file(files, MANIFEST)["version"]
            .as_str()
            .unwrap()
            .into()
    }
    fn absolute_command() -> String {
        std::env::current_exe()
            .unwrap()
            .with_file_name("decision with spaces")
            .to_str()
            .unwrap()
            .into()
    }

    #[test]
    fn modes_keep_complete_skill_and_only_selected_interface() {
        let skill = skill::files().unwrap();
        for integration in ["cli", "mcp"] {
            let files = files(integration, "decision", &[]);
            let manifest = json_file(&files, MANIFEST);
            assert_eq!(manifest["skills"], "./skills/decision");
            assert_eq!(manifest["requires_local_executor"], true);
            let root = Path::new(PACKAGE).join("skills/decision");
            let names: BTreeSet<_> = files
                .keys()
                .filter_map(|name| name.strip_prefix(&root).ok().map(Path::to_path_buf))
                .collect();
            assert_eq!(names, skill.keys().cloned().collect());
            let text = String::from_utf8_lossy(&files[&root.join("SKILL.md")]);
            assert!(text.contains(&format!(
                "This plugin uses {} integration.",
                integration.to_uppercase()
            )));
            if integration == "mcp" {
                assert_eq!(manifest["mcpServers"], "./.mcp.json");
                let server = &json_file(&files, MCP)["mcpServers"]["decision"];
                assert_eq!(server["command"], "decision");
                assert_eq!(server["args"], json!(["mcp"]));
                let names = server["env_vars"].as_array().unwrap();
                for name in ENV_VARS {
                    assert!(names.contains(&Value::from(*name)));
                }
            } else {
                assert!(manifest.get("mcpServers").is_none());
                assert!(!files.contains_key(Path::new(MCP)));
                assert!(!text.contains("Prefer this tool when available"));
            }
        }
    }

    #[test]
    fn cache_version_tracks_content_mode_command_and_names() {
        let original = version(&files("mcp", "decision", &[]));
        for variant in [
            files("cli", "decision", &[]),
            files("mcp", &absolute_command(), &[]),
            files("mcp", "decision", &["CUSTOM_KEY".into()]),
        ] {
            assert_ne!(version(&variant), original);
        }
        let mut altered = skill::files().unwrap();
        altered
            .get_mut(Path::new("references/examples.md"))
            .unwrap()
            .extend_from_slice(b"\nChanged example.\n");
        assert_ne!(
            version(&plugin_files(altered, "0.10.0", "mcp", "decision", &[]).unwrap()),
            original
        );
        assert_eq!(
            files(
                "mcp",
                "decision",
                &["CUSTOM_KEY".into(), "CUSTOM_KEY".into()]
            ),
            files("mcp", "decision", &["CUSTOM_KEY".into()])
        );
        assert_eq!(
            files("mcp", "decision", &["Z_KEY".into(), "A_KEY".into()]),
            files("mcp", "decision", &["A_KEY".into(), "Z_KEY".into()])
        );
    }

    #[test]
    fn cache_hash_matches_characterized_python_algorithm() {
        let minimal_skill = [(PathBuf::from("SKILL.md"), b"# Decision\n".to_vec())]
            .into_iter()
            .collect();
        let output = plugin_files(minimal_skill, "0.10.0", "mcp", "decision", &[]).unwrap();
        assert_eq!(version(&output), "0.10.0+1efdfc63dd13cb35");
    }

    #[test]
    fn names_are_forwarded_without_reading_values() {
        let output = files("mcp", "decision", &["PRIVATE_KEY".into()]);
        let server = &json_file(&output, MCP)["mcpServers"]["decision"];
        assert!(server.get("env").is_none());
        assert!(
            server["env_vars"]
                .as_array()
                .unwrap()
                .contains(&json!("PRIVATE_KEY"))
        );
        assert!(
            server
                .as_object()
                .unwrap()
                .keys()
                .all(|key| ["command", "args", "env_vars"].contains(&key.as_str()))
        );
        assert_eq!(output, files("mcp", "decision", &["PRIVATE_KEY".into()]));
    }

    #[test]
    fn invalid_configuration_is_rejected_before_creating_output() {
        for (integration, command, names) in [
            ("other", "decision", vec![]),
            ("cli", "decision", vec!["CUSTOM_KEY".into()]),
            ("cli", absolute_command().as_str(), vec![]),
            ("mcp", "", vec![]),
            ("mcp", "decision\nother", vec![]),
            ("mcp", "./decision", vec![]),
            ("mcp", "python -m decision_cli.cli", vec![]),
            ("mcp", "decision", vec!["KEY=secret".into()]),
            ("mcp", "decision", vec!["bad-name".into()]),
            ("mcp", "decision", vec!["9_KEY".into()]),
        ] {
            let temp = tempfile::tempdir().unwrap();
            let output = temp.path().join("output");
            assert!(export(&output, integration, command, &names, false).is_err());
            assert!(!output.exists());
        }
        files("mcp", &absolute_command(), &[]);
    }

    #[test]
    fn mode_switch_removes_mcp_and_check_detects_drift_without_writes() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("plugin");
        let mcp = files("mcp", "decision", &[]);
        let cli = files("cli", "decision", &[]);
        assert!(export_files(&mcp, &root, true).is_err());
        assert!(!root.exists());
        export_files(&mcp, &root, false).unwrap();
        export_files(&mcp, &root, true).unwrap();
        export_files(&mcp, &root, false).unwrap();
        export_files(&cli, &root, false).unwrap();
        assert!(!root.join(MCP).exists());
        export_files(&cli, &root, true).unwrap();
        assert!(export_files(&mcp, &root, true).is_err());
        fs::write(root.join(MANIFEST), b"{}\n").unwrap();
        assert!(export_files(&cli, &root, true).is_err());
        assert_eq!(fs::read(root.join(MANIFEST)).unwrap(), b"{}\n");
    }

    #[test]
    fn unmanaged_collisions_and_extra_files_are_preserved() {
        for name in ["README.md", MANIFEST, CATALOG] {
            let temp = tempfile::tempdir().unwrap();
            let target = temp.path().join(name);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::write(&target, b"User-owned content\n").unwrap();
            assert!(export_files(&files("mcp", "decision", &[]), temp.path(), false).is_err());
            assert_eq!(fs::read(target).unwrap(), b"User-owned content\n");
            assert_eq!(skill::existing_files(temp.path()).unwrap().len(), 1);
        }
        let temp = tempfile::tempdir().unwrap();
        export_files(&files("mcp", "decision", &[]), temp.path(), false).unwrap();
        fs::write(temp.path().join("private.txt"), b"User-owned data").unwrap();
        let before = fs::read(temp.path().join(MANIFEST)).unwrap();
        assert!(export_files(&files("cli", "decision", &[]), temp.path(), false).is_err());
        assert_eq!(
            fs::read(temp.path().join("private.txt")).unwrap(),
            b"User-owned data"
        );
        assert_eq!(fs::read(temp.path().join(MANIFEST)).unwrap(), before);
        assert!(temp.path().join(MCP).exists());
    }

    #[cfg(unix)]
    #[test]
    fn symlink_roots_and_entries_are_rejected_without_touching_target() {
        use std::os::unix::fs::symlink;
        let temp = tempfile::tempdir().unwrap();
        let target = temp.path().join("target");
        fs::create_dir(&target).unwrap();
        let root = temp.path().join("link");
        symlink(&target, &root).unwrap();
        assert!(export_files(&files("mcp", "decision", &[]), &root, false).is_err());
        let output = temp.path().join("output");
        fs::create_dir(&output).unwrap();
        symlink(&target, output.join("plugins")).unwrap();
        assert!(export_files(&files("mcp", "decision", &[]), &output, false).is_err());
        assert!(fs::read_dir(target).unwrap().next().is_none());
    }
}
