use crate::{Error, Result};
use std::{path::Path, process::Stdio, sync::OnceLock};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::Command,
};
use tokio_util::task::TaskTracker;

pub const INHERITED_ENV: &[&str] = &[
    "PATH",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
];
fn reapers() -> &'static TaskTracker {
    static REAPERS: OnceLock<TaskTracker> = OnceLock::new();
    REAPERS.get_or_init(TaskTracker::new)
}
pub async fn drain() {
    reapers().close();
    reapers().wait().await;
}

#[cfg(unix)]
mod platform {
    use super::*;
    use tokio::process::{Child, ChildStdin, ChildStdout};
    pub struct Managed {
        id: u32,
        child: Option<Child>,
    }
    impl Managed {
        pub fn spawn(mut cmd: Command) -> Result<Self> {
            let child = cmd.process_group(0).spawn().map_err(|_| Error::Provider)?;
            Ok(Self {
                id: child.id().ok_or(Error::Provider)?,
                child: Some(child),
            })
        }
        pub fn stdin(&mut self) -> ChildStdin {
            self.child.as_mut().unwrap().stdin.take().unwrap()
        }
        pub fn stdout(&mut self) -> ChildStdout {
            self.child.as_mut().unwrap().stdout.take().unwrap()
        }
        pub async fn wait(&mut self) -> Result<std::process::ExitStatus> {
            let status = self
                .child
                .as_mut()
                .unwrap()
                .wait()
                .await
                .map_err(|_| Error::Provider)?;
            self.child.take();
            Ok(status)
        }
    }
    impl Drop for Managed {
        fn drop(&mut self) {
            // Includes descendants that outlive the command or keep pipes open.
            unsafe {
                libc::kill(-(self.id as i32), libc::SIGKILL);
            }
            if let Some(mut child) = self.child.take() {
                let _ = child.start_kill();
                reapers().spawn(async move {
                    let _ = child.wait().await;
                });
            }
        }
    }
}
#[cfg(windows)]
mod platform {
    use super::*;
    use process_wrap::tokio::{ChildWrapper, CommandWrap, JobObject, KillOnDrop};
    use tokio::process::{ChildStdin, ChildStdout};
    pub struct Managed {
        child: Option<Box<dyn ChildWrapper>>,
    }
    impl Managed {
        pub fn spawn(cmd: Command) -> Result<Self> {
            // The wrapper creates the process suspended, assigns it to a
            // kill-on-close job, then resumes it. Descendants cannot escape the
            // assignment window by starting before the job exists.
            let mut command = CommandWrap::from(cmd);
            let child = command
                .wrap(JobObject)
                .wrap(KillOnDrop)
                .spawn()
                .map_err(|_| Error::Provider)?;
            Ok(Self { child: Some(child) })
        }
        pub fn stdin(&mut self) -> ChildStdin {
            self.child.as_mut().unwrap().stdin().take().unwrap()
        }
        pub fn stdout(&mut self) -> ChildStdout {
            self.child.as_mut().unwrap().stdout().take().unwrap()
        }
        pub async fn wait(&mut self) -> Result<std::process::ExitStatus> {
            let child = self.child.as_mut().unwrap();
            // Wait for the direct child first, then terminate any remaining job
            // members and wait for job completion before dropping its handles.
            let status = child
                .inner_mut()
                .wait()
                .await
                .map_err(|_| Error::Provider)?;
            let _ = child.start_kill();
            child.wait().await.map_err(|_| Error::Provider)?;
            self.child.take();
            Ok(status)
        }
    }
    impl Drop for Managed {
        fn drop(&mut self) {
            if let Some(mut child) = self.child.take() {
                let _ = child.start_kill();
                reapers().spawn(async move {
                    let _ = child.wait().await;
                });
            }
        }
    }
}
async fn bounded(mut r: impl AsyncRead + Unpin, limit: usize) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    let mut buf = [0u8; 8192];
    loop {
        let n = r.read(&mut buf).await.map_err(|_| Error::Provider)?;
        if n == 0 {
            return Ok(output);
        }
        if output.len() + n > limit {
            return Err(Error::Provider);
        }
        output.extend_from_slice(&buf[..n]);
    }
}
pub async fn execute(root: &Path, argv: &[String], input: &[u8], limit: usize) -> Result<Vec<u8>> {
    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..])
        .current_dir(root)
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    for name in INHERITED_ENV {
        if let Some(value) = std::env::var_os(name) {
            cmd.env(name, value);
        }
    }
    let mut child = platform::Managed::spawn(cmd)?;
    let mut stdin = child.stdin();
    let stdout = child.stdout();
    let write = async {
        stdin.write_all(input).await.map_err(|_| Error::Provider)?;
        drop(stdin);
        Ok(())
    };
    let ((), output) = tokio::try_join!(write, bounded(stdout, limit))?;
    if !child.wait().await?.success() {
        return Err(Error::Provider);
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "subprocess fixture"]
    fn descendant_fixture() {
        std::thread::sleep(std::time::Duration::from_secs(60));
    }
    #[test]
    #[ignore = "subprocess fixture"]
    fn tree_fixture() {
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--ignored", "--exact", "process::tests::descendant_fixture"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        std::fs::write(
            "pids.json",
            format!("[{},{}]", std::process::id(), child.id()),
        )
        .unwrap();
        std::thread::sleep(std::time::Duration::from_secs(60));
        let _ = child.wait();
    }
    fn running(pid: u32) -> bool {
        #[cfg(unix)]
        {
            let result = std::process::Command::new("ps")
                .args(["-o", "stat=", "-p", &pid.to_string()])
                .output()
                .unwrap();
            let state = String::from_utf8_lossy(&result.stdout);
            !state.trim().is_empty() && !state.trim().starts_with('Z')
        }
        #[cfg(windows)]
        {
            let tasklist = std::path::PathBuf::from(std::env::var_os("SYSTEMROOT").unwrap())
                .join("System32/tasklist.exe");
            let output = std::process::Command::new(tasklist)
                .args(["/FI", &format!("PID eq {pid}"), "/NH"])
                .output()
                .unwrap();
            String::from_utf8_lossy(&output.stdout)
                .split_whitespace()
                .any(|s| s == pid.to_string())
        }
    }
    #[tokio::test]
    async fn cancellation_terminates_and_reaps_tree() {
        let root = tempfile::tempdir().unwrap();
        let args = vec![
            std::env::current_exe()
                .unwrap()
                .to_string_lossy()
                .into_owned(),
            "--ignored".into(),
            "--exact".into(),
            "process::tests::tree_fixture".into(),
        ];
        let mut work = Box::pin(execute(root.path(), &args, b"", 1 << 20));
        let marker = root.path().join("pids.json");
        tokio::time::timeout(std::time::Duration::from_secs(10),async {
            while !marker.exists() {tokio::select! {r=&mut work=>panic!("child exited: {r:?}"),_=tokio::time::sleep(std::time::Duration::from_millis(10))=>{}}}
        }).await.unwrap();
        let pids: Vec<u32> = serde_json::from_slice(&std::fs::read(marker).unwrap()).unwrap();
        drop(work);
        drain().await;
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            while pids.iter().any(|p| running(*p)) {
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        #[cfg(unix)]
        unsafe {
            let mut status = 0;
            assert_eq!(
                libc::waitpid(pids[0] as i32, &mut status, libc::WNOHANG),
                -1
            );
            assert_eq!(
                std::io::Error::last_os_error().raw_os_error(),
                Some(libc::ECHILD)
            );
        }
    }
}
