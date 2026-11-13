use crate::{Error, Result};
use std::{path::Path, process::Stdio, sync::OnceLock};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::{Child, Command},
};
use tokio_util::task::TaskTracker;

pub const INHERITED_ENV: &[&str] = &["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"];
fn reapers() -> &'static TaskTracker {
    static REAPERS: OnceLock<TaskTracker> = OnceLock::new();
    REAPERS.get_or_init(TaskTracker::new)
}
pub async fn drain() {
    reapers().close();
    reapers().wait().await;
}
struct ProcessGroup {
    id: u32,
    child: Option<Child>,
}
impl Drop for ProcessGroup {
    fn drop(&mut self) {
        // A child can leave descendants holding stdout even after its own exit.
        // The guard also runs when rmcp cancellation drops the operation future.
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
        .kill_on_drop(true)
        .process_group(0);
    for name in INHERITED_ENV {
        if let Some(value) = std::env::var_os(name) {
            cmd.env(name, value);
        }
    }
    let mut child = cmd.spawn().map_err(|_| Error::Provider)?;
    let mut stdin = child.stdin.take().unwrap();
    let stdout = child.stdout.take().unwrap();
    let mut group = ProcessGroup {
        id: child.id().ok_or(Error::Provider)?,
        child: Some(child),
    };
    let write = async {
        stdin.write_all(input).await.map_err(|_| Error::Provider)?;
        drop(stdin);
        Ok(())
    };
    let read = bounded(stdout, limit);
    let ((), output) = tokio::try_join!(write, read)?;
    let status = group
        .child
        .as_mut()
        .unwrap()
        .wait()
        .await
        .map_err(|_| Error::Provider)?;
    group.child.take();
    drop(group);
    if !status.success() {
        return Err(Error::Provider);
    }
    Ok(output)
}
