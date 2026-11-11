"""Human-readable CLI output, independent of MCP and JSON serialization."""

import json


def text(value):
    if isinstance(value, str):
        return "".join(char if char.isprintable() else f"\\u{ord(char):04x}" for char in value)
    return json.dumps(value, ensure_ascii=False)


def render_result(result):
    lines = [f"Model: {text(result['model'])}"]
    for name, answer in result["answers"].items():
        lines.extend(["", f"{text(name)}:"])
        kind = answer["type"]
        if kind == "noul":
            lines.append(f"  Yes probability: {answer['noul']:.1%}")
        elif kind == "choice":
            lines.append(f"  Choice: {text(answer['choice'])}")
        elif kind == "score":
            lines.append(f"  Score: {answer['score']:.6g}")
        elif kind == "box":
            box = answer["box"]
            lines.append("  Box: " + ", ".join(f"{edge}={box[edge]:.6g}" for edge in ("left", "top", "right", "bottom")))
        if answer.get("confidence") is not None:
            lines.append(f"  Confidence: {answer['confidence']:.1%}")
        if answer.get("probabilities"):
            lines.append("  Probabilities:")
        for option, probability in answer.get("probabilities", {}).items():
            label = answer.get("legend", {}).get(option, option)
            lines.append(f"    {text(label)}: {probability:.1%}")
    usage = result.get("usage", {})
    counts = [f"{usage[key]} {label} tokens" for key, label in (("input_tokens", "input"), ("output_tokens", "output"))
              if usage.get(key) is not None]
    if counts:
        lines.extend(["", "Usage: " + ", ".join(counts)])
    return "\n".join(lines)


def render_preview(payload, images):
    lines = ["Request preview (not sent)", f"Model: {text(payload.get('model', 'provider default'))}",
             "", "State:", json.dumps(payload["state"], indent=2, ensure_ascii=False), "", "Questions:"]
    for name, question in payload["questions"].items():
        lines.append(f"  {text(name)} [{question['type']}]: {text(question['instructions'])}")
        if "criteria" in question:
            lines.append("    Criteria: " + text(question["criteria"]))
    if images:
        lines.extend(["", "Images:"])
        for name, data in images.items():
            source = payload["assets"][name].get("path", "base64 input")
            lines.append(f"  {text(name)}: {text(source)} ({len(data)} bytes)")
    return "\n".join(lines)
