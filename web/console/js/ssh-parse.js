// Parse a pasted SSH command (e.g. "ssh -p 10526 root@connect.host.com")
// into host / port / username. Also accepts bare "user@host",
// "user@host:port", or "host". Returns null when nothing matches.

export function parseSshCommand(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;

  if (!/^ssh(\s|$)/i.test(raw)) {
    const m = /^(?:([\w.-]+)@)?\[?([\w.-]+(?:\.[\w.-]+)*)\]?(?::(\d+))?$/.exec(raw);
    if (!m) return null;
    return { username: m[1] || "", host: m[2], port: m[3] ? Number(m[3]) : null };
  }

  const tokens = raw.split(/\s+/).slice(1);
  let port = null;
  let target = "";
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    if (/^-p$/.test(tok)) {
      port = Number(tokens[i + 1]) || null;
      i += 1;
      continue;
    }
    if (/^-/.test(tok)) continue;           // ignore other flags (and values heuristically)
    if (target) continue;
    if (i > 0 && /^-/.test(tokens[i - 1]) && !/^-\d+$/.test(tokens[i - 1]) && tokens[i - 1] !== "-p" && !/^@?[\w.-]+@/.test(tok)) {
      // This token is likely the previous flag's value, not the destination.
      continue;
    }
    target = tok;
  }
  if (!target) return null;

  let username = "";
  let host = target;
  const at = target.lastIndexOf("@");
  if (at >= 0) {
    username = target.slice(0, at);
    host = target.slice(at + 1);
  }
  if (/^\[.*\]$/.test(host)) host = host.slice(1, -1);
  if (!port && /:\d+$/.test(host)) {
    const colon = host.lastIndexOf(":");
    port = Number(host.slice(colon + 1)) || null;
    host = host.slice(0, colon);
  }
  if (!host) return null;
  return { username, host, port };
}
