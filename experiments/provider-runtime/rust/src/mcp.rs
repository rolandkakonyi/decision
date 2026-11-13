use crate::{
    Error, Options, Result,
    json::{FRAME_LIMIT, decode},
    provider,
    store::{self, Plugin},
    timed,
};
use futures::StreamExt;
use rmcp::{
    ErrorData, RoleServer, ServerHandler, ServiceExt,
    model::*,
    service::{RequestContext, RxJsonRpcMessage, TxJsonRpcMessage},
    transport::async_rw::JsonRpcMessageCodec,
};
use serde_json::{Value, json};
use std::{path::Path, sync::Arc};
use tokio_util::codec::{FramedRead, FramedWrite, LinesCodec};

struct Host {
    options: Options,
    plugin: Option<Plugin>,
}
fn tool(name: &str, description: &str, input: Value, output: Option<Value>) -> Tool {
    let mut t = Tool::new(
        name.to_owned(),
        description.to_owned(),
        Arc::new(input.as_object().unwrap().clone()),
    );
    t.output_schema = output.map(|v| Arc::new(v.as_object().unwrap().clone()));
    t
}
impl Host {
    fn tools(&self) -> Vec<Tool> {
        if self.options.management {
            vec![
                tool(
                    "provider_install",
                    "Install a trusted provider package; setup may execute.",
                    json!({"type":"object","required":["source"],"additionalProperties":false,"properties":{"source":{"type":"string"},"sha256":{"type":"string"}}}),
                    None,
                ),
                tool(
                    "provider_list",
                    "List registered provider versions.",
                    json!({"type":"object","additionalProperties":false}),
                    None,
                ),
                tool(
                    "provider_remove",
                    "Unregister a provider; pinned sessions retain their artifact.",
                    json!({"type":"object","required":["name"],"additionalProperties":false,"properties":{"name":{"type":"string"}}}),
                    None,
                ),
            ]
        } else {
            let mut input: Value =
                serde_json::from_str(include_str!("../request.schema.json")).unwrap();
            if let Some(p) = &self.plugin
                && !p.manifest.schema_constraints.is_empty()
            {
                input["allOf"] = json!([p.manifest.schema_constraints]);
            }
            let output = serde_json::from_str(include_str!("../response.schema.json")).unwrap();
            let mut t = tool(
                "evaluate",
                "Ask typed questions about shared context in one provider request.",
                input,
                Some(output),
            );
            t.annotations = Some(
                ToolAnnotations::new()
                    .read_only(true)
                    .destructive(false)
                    .open_world(true),
            );
            vec![t]
        }
    }
}
impl ServerHandler for Host {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("decision-prototype-rust", "1"))
    }
    async fn list_tools(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> std::result::Result<ListToolsResult, ErrorData> {
        Ok(ListToolsResult {
            tools: self.tools(),
            ..Default::default()
        })
    }
    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        context: RequestContext<RoleServer>,
    ) -> std::result::Result<CallToolResponse, ErrorData> {
        let args = Value::Object(request.arguments.unwrap_or_default());
        let work = async {
            if self.options.management {
                store::management(&self.options, request.name.as_ref(), args).await
            } else if request.name == "evaluate" {
                provider::evaluate(
                    &self.options,
                    self.plugin.as_ref(),
                    args,
                    false,
                    Path::new(""),
                )
                .await
            } else {
                Err(Error::Input)
            }
        };
        let result = tokio::select! {
            _ = context.ct.cancelled() => Err(Error::Provider),
            r = timed(&self.options,work) => r,
        };
        let response = match result {
            Ok(v) => {
                let mut r = CallToolResult::success(vec![ContentBlock::text(v.to_string())]);
                if v.is_object() {
                    r.structured_content = Some(v);
                }
                r
            }
            Err(e) => CallToolResult::error(vec![ContentBlock::text(e.to_string())]),
        };
        Ok(response.into())
    }
}
pub async fn serve(options: Options, plugin: Option<Plugin>) -> Result<()> {
    // rmcp owns protocol negotiation, dispatch, cancellation and responses. The
    // bounded input adapter rejects duplicate keys before SDK Value decoding.
    let input = FramedRead::new(
        tokio::io::stdin(),
        LinesCodec::new_with_max_length(FRAME_LIMIT),
    )
    .map(|line| {
        line.ok()
            .and_then(|s| decode(s.as_bytes()).ok())
            .and_then(|v| serde_json::from_value::<RxJsonRpcMessage<RoleServer>>(v).ok())
    })
    .take_while(|message| futures::future::ready(message.is_some()))
    .map(Option::unwrap);
    let output = FramedWrite::new(
        tokio::io::stdout(),
        JsonRpcMessageCodec::<TxJsonRpcMessage<RoleServer>>::default(),
    );
    let server = Host { options, plugin }
        .serve((output, input))
        .await
        .map_err(|_| Error::Provider)?;
    server.waiting().await.map_err(|_| Error::Provider)?;
    Ok(())
}
