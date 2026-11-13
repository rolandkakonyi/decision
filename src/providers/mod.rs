//! Native built-in provider adapters. Common policy remains in the engine.

mod cloudflare;
mod common;
mod gemini;
mod openai;

use crate::{Error, Options, Result};
use serde_json::{Value, json};

pub struct Info {
    pub default_base_url: &'static str,
    pub default_model: Option<&'static str>,
    pub base_url_env: &'static str,
    pub credential_env_vars: &'static [&'static str],
}

pub fn info(name: &str) -> Option<Info> {
    Some(match name {
        "typesafe" => Info {
            default_base_url: "https://api.typesafe.ai",
            default_model: None,
            base_url_env: "TYPESAFE_BASE_URL",
            credential_env_vars: &["TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY"],
        },
        "openai" => Info {
            default_base_url: "https://api.openai.com/v1",
            default_model: Some(openai::DEFAULT_MODEL),
            base_url_env: "OPENAI_BASE_URL",
            credential_env_vars: &["OPENAI_API_KEY"],
        },
        "cloudflare" => Info {
            default_base_url: cloudflare::API_ROOT,
            default_model: Some("clef"),
            base_url_env: "CLOUDFLARE_BASE_URL",
            credential_env_vars: &["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AUTH_TOKEN"],
        },
        "gemini" => Info {
            default_base_url: "https://generativelanguage.googleapis.com/v1beta",
            default_model: Some(gemini::DEFAULT_MODEL),
            base_url_env: "GEMINI_BASE_URL",
            credential_env_vars: &["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        },
        _ => return None,
    })
}

pub fn prepare(name: &str, request: &mut Value) -> Result<()> {
    let provider = info(name).ok_or_else(|| Error::input("Unknown built-in provider"))?;
    if request.get("model").is_none()
        && let Some(model) = provider.default_model
    {
        request["model"] = model.into();
    }
    match name {
        "typesafe" => Ok(()),
        "openai" => openai::validate(request),
        "cloudflare" => cloudflare::prepare(request),
        "gemini" => gemini::validate(request),
        _ => unreachable!(),
    }
}

pub fn validate_connection(name: &str, request: &Value, options: &Options) -> Result<()> {
    match name {
        "cloudflare" => cloudflare::endpoint(options, common::model(request, "clef")).map(|_| ()),
        "typesafe" | "openai" | "gemini" => Ok(()),
        _ => Err(Error::input("Unknown built-in provider")),
    }
}

pub fn schema_constraints(name: &str) -> Value {
    match name {
        "openai" => common::constraints(400, 10, None, None),
        "cloudflare" => {
            let mut schema =
                common::constraints(64, 10, Some(cloudflare::QUESTION_ID_PATTERN), None);
            schema["properties"]["model"] = json!({"enum":["clef","clef-flash","@cf/cloudflare/clef","@cf/cloudflare/clef-flash"]});
            schema["properties"]["assets"] = json!({"maxProperties":4,"description":"Clef: at most 4 images, 4 MiB each and 8 MiB combined.","additionalProperties":{"properties":{"data_base64":{"maxLength":4 * cloudflare::MAX_IMAGE_BYTES.div_ceil(3)}}}});
            schema
        }
        "gemini" => common::constraints(1000, 255, Some(gemini::QUESTION_ID_PATTERN), Some(128)),
        _ => json!({}),
    }
}

pub async fn evaluate(
    name: &str,
    request: &Value,
    options: &Options,
    credential: &str,
) -> Result<Value> {
    match name {
        "openai" => openai::evaluate(request, options, credential).await,
        "cloudflare" => cloudflare::evaluate(request, options, credential).await,
        "gemini" => gemini::evaluate(request, options, credential).await,
        _ => Err(Error::execution("Unsupported built-in provider transport")),
    }
}

#[cfg(test)]
mod tests;
