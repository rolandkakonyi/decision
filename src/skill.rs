//! Portable companion-skill export, generated without provider discovery.

use crate::{Error, Result};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
};

pub(crate) type Files = BTreeMap<PathBuf, Vec<u8>>;

pub fn files() -> Result<Files> {
    let mut files = BTreeMap::new();
    files.insert(
        PathBuf::from("SKILL.md"),
        include_str!("templates/skill/SKILL.md.in")
            .replace("@VERSION@", env!("CARGO_PKG_VERSION"))
            .into_bytes(),
    );
    macro_rules! reference {
        ($name:literal) => {
            files.insert(
                PathBuf::from(concat!("references/", $name)),
                include_bytes!(concat!("templates/skill/references/", $name)).to_vec(),
            );
        };
    }
    reference!("request.json");
    reference!("images.json");
    reference!("examples.md");
    reference!("providers.md");
    reference!("dog-request.json");
    reference!("dog-questions.json");
    reference!("dog-mixed-request.json");
    reference!("dog-mixed-questions.json");
    reference!("openai-dog-request.json");
    reference!("openai-dog-questions.json");
    reference!("cloudflare-dog-request.json");
    reference!("cloudflare-dog-questions.json");
    reference!("gemini-dog-request.json");
    reference!("gemini-dog-questions.json");
    let schema: serde_json::Value = serde_json::from_str(include_str!("../request.schema.json"))
        .map_err(|_| Error::execution("Cannot generate request schema"))?;
    files.insert(
        PathBuf::from("references/request-schema.json"),
        encoded(&schema)?,
    );
    files.insert(
        PathBuf::from("references/cli-help.txt"),
        crate::cli::help_text().into_bytes(),
    );
    files.insert(
        PathBuf::from("references/plugin.md"),
        crate::plugin::GUIDE.as_bytes().to_vec(),
    );
    Ok(files)
}

pub fn export(output: Option<&Path>, check: bool) -> Result<()> {
    let files = files()?;
    let Some(output) = output else {
        if check {
            return Err(Error::input("--check requires --output"));
        }
        return io::stdout()
            .lock()
            .write_all(&files[Path::new("SKILL.md")])
            .map_err(|_| Error::execution("Cannot write generated skill"));
    };
    export_files(&files, output, check)
}

fn export_files(files: &Files, output: &Path, check: bool) -> Result<()> {
    // Validate the tree before any write so managed paths cannot follow links.
    existing_files(output)?;
    if check {
        if files
            .iter()
            .any(|(name, content)| fs::read(output.join(name)).ok().as_ref() != Some(content))
        {
            return Err(Error::input("Generated skill is stale; regenerate it"));
        }
        return Ok(());
    }
    write_files(output, files)
}

pub(crate) fn encoded(value: &serde_json::Value) -> Result<Vec<u8>> {
    let mut data = serde_json::to_vec_pretty(value)
        .map_err(|_| Error::execution("Cannot encode generated export"))?;
    data.push(b'\n');
    Ok(data)
}

pub(crate) fn existing_files(root: &Path) -> Result<BTreeSet<PathBuf>> {
    fn walk(root: &Path, relative: &Path, found: &mut BTreeSet<PathBuf>) -> Result<()> {
        let path = if relative.as_os_str().is_empty() {
            root.to_path_buf()
        } else {
            root.join(relative)
        };
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error)
                if relative.as_os_str().is_empty() && error.kind() == io::ErrorKind::NotFound =>
            {
                return Ok(());
            }
            Err(_) => return Err(Error::input("Cannot inspect export output")),
        };
        if metadata.file_type().is_symlink() {
            return Err(Error::input("Export output cannot contain symlinks"));
        }
        if metadata.is_file() && !relative.as_os_str().is_empty() {
            found.insert(relative.into());
        } else if metadata.is_dir() {
            for entry in
                fs::read_dir(path).map_err(|_| Error::input("Cannot inspect export output"))?
            {
                let entry = entry.map_err(|_| Error::input("Cannot inspect export output"))?;
                walk(root, &relative.join(entry.file_name()), found)?;
            }
        } else {
            return Err(Error::input(
                "Export output must contain only files and directories",
            ));
        }
        Ok(())
    }
    // A trailing separator makes symlink_metadata follow a directory link on
    // POSIX. Components remove that separator without resolving the link.
    let mut root: PathBuf = root.components().collect();
    if root.as_os_str().is_empty() {
        root.push(".");
    }
    let mut found = BTreeSet::new();
    walk(&root, Path::new(""), &mut found)?;
    Ok(found)
}

pub(crate) fn write_files(root: &Path, files: &Files) -> Result<()> {
    for (name, content) in files {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap())
            .map_err(|_| Error::input("Cannot create export directory"))?;
        fs::write(path, content).map_err(|_| Error::input("Cannot write generated export"))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_files_use_running_version_help_and_common_schema() {
        let output = files().unwrap();
        assert_eq!(output.len(), 18);
        let skill = String::from_utf8_lossy(&output[Path::new("SKILL.md")]);
        assert!(skill.contains(&format!(
            "Generated by decision {}",
            env!("CARGO_PKG_VERSION")
        )));
        assert!(!skill.contains("@VERSION@"));
        assert!(!skill.contains("uv tool install"));
        assert!(!skill.contains("same Python environment"));
        assert!(skill.contains("references/providers.md"));
        assert_eq!(
            output[Path::new("references/cli-help.txt")],
            crate::cli::help_text().as_bytes()
        );
        let schema: serde_json::Value =
            serde_json::from_slice(&output[Path::new("references/request-schema.json")]).unwrap();
        let common: serde_json::Value =
            serde_json::from_str(include_str!("../request.schema.json")).unwrap();
        assert_eq!(schema, common);
        for (path, data) in &output {
            assert!(
                data.ends_with(b"\n"),
                "{} needs an EOF newline",
                path.display()
            );
            if path
                .extension()
                .is_some_and(|extension| extension == "json")
            {
                serde_json::from_slice::<serde_json::Value>(data).unwrap();
            }
        }
    }

    #[test]
    fn skill_export_is_idempotent_and_check_does_not_write() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("decision");
        let output = files().unwrap();
        assert!(export_files(&output, &root, true).is_err());
        assert!(!root.exists());
        export_files(&output, &root, false).unwrap();
        fs::write(root.join("personal-notes.txt"), b"keep me").unwrap();
        export_files(&output, &root, true).unwrap();
        export_files(&output, &root, false).unwrap();
        assert_eq!(
            fs::read(root.join("personal-notes.txt")).unwrap(),
            b"keep me"
        );
        fs::write(root.join("SKILL.md"), b"modified").unwrap();
        assert!(export_files(&output, &root, true).is_err());
        assert_eq!(fs::read(root.join("SKILL.md")).unwrap(), b"modified");
        export_files(&output, &root, false).unwrap();
        export_files(&output, &root, true).unwrap();
    }

    #[test]
    fn skill_check_requires_output() {
        assert!(export(None, true).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn skill_rejects_symlink_before_writing() {
        use std::os::unix::fs::symlink;
        let temp = tempfile::tempdir().unwrap();
        let target = temp.path().join("target");
        fs::create_dir(&target).unwrap();
        let root = temp.path().join("decision");
        fs::create_dir(&root).unwrap();
        symlink(&target, root.join("references")).unwrap();
        assert!(export_files(&files().unwrap(), &root, false).is_err());
        assert!(!root.join("SKILL.md").exists());
        assert!(fs::read_dir(target).unwrap().next().is_none());
    }
}
