//! Native built-in provider adapters. Common policy remains in the engine.

use crate::{Options, Result};
use serde_json::Value;

pub struct Info {
    pub default_base_url: &'static str,
    pub default_model: Option<&'static str>,
    pub base_url_env: &'static str,
    pub credential_env_vars: &'static [&'static str],
}

pub fn info(_name: &str) -> Option<Info> { None }
pub fn prepare(_name: &str, _request: &mut Value) -> Result<()> { Ok(()) }
pub fn validate_connection(_name: &str, _request: &Value, _options: &Options) -> Result<()> { Ok(()) }
pub fn schema_constraints(_name: &str) -> Value { serde_json::json!({}) }
pub async fn evaluate(_name: &str, _request: &Value, _options: &Options, _credential: &str) -> Result<Value> {
    Err(crate::Error::Provider)
}
