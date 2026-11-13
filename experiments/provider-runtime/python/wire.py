"""Strict, bounded JSON and external process execution."""
import asyncio
import json
import os
import signal

LIMIT = 48 * 1024 * 1024


def loads(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = item
        return result
    def invalid(_):
        raise ValueError("Nonfinite JSON number")
    result = json.loads(value, object_pairs_hook=pairs, parse_constant=invalid)
    # Overflowing decimal exponents also become nonfinite floats.
    json.dumps(result, allow_nan=False)
    return result


def environment():
    return {key: os.environ[key] for key in (
        "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "SYSTEMROOT"
    ) if key in os.environ}


async def operation(package, manifest, method, timeout, **kwargs):
    from decision_cli.errors import ProviderError
    encoded = (json.dumps(dict(protocol_version=1, method=method, **kwargs), allow_nan=False) + "\n").encode()
    if len(encoded) > LIMIT:
        raise ValueError("Provider frame exceeds limit")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *manifest["command"], cwd=package, env=environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True, limit=LIMIT)
        async def exchange():
            async def write():
                process.stdin.write(encoded)
                await process.stdin.drain()
                process.stdin.close()
            async def read():
                line = await process.stdout.readuntil(b"\n")
                if len(line) > LIMIT:
                    raise ValueError("Provider frame exceeds limit")
                extra = await process.stdout.read(1)
                if extra:
                    raise ValueError("Unexpected provider output")
                return line
            _, line = await asyncio.gather(write(), read())
            if await process.wait() != 0:
                raise ValueError("Provider command failed")
            response = loads(line)
            if (not isinstance(response, dict) or type(response.get("protocol_version")) is not int
                    or response["protocol_version"] != 1 or type(response.get("ok")) is not bool):
                raise ValueError("Invalid provider envelope")
            if not response["ok"]:
                error = response.get("error")
                if isinstance(error, dict) and error.get("code") == "invalid_request":
                    raise ValueError("Provider rejected request")
                raise ProviderError("Provider execution failed")
            return response["result"]
        return await asyncio.wait_for(exchange(), timeout)
    except asyncio.CancelledError:
        raise
    except ValueError as error:
        if str(error) == "Provider rejected request":
            raise
        raise ProviderError("Invalid provider protocol response") from None
    except Exception:
        raise ProviderError("Provider execution failed or timed out") from None
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
