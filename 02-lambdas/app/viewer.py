"""The inline file viewer: an MCP Apps UI resource.

ChatGPT does not draw an MCP image content block for the user -- it hands the
image to the model and shows the person nothing -- and it does not render a
markdown image from an arbitrary URL either. What it does render is an MCP
Apps UI resource: a small HTML page, served by this server, that the host
loads in a sandboxed frame inline in the conversation and feeds the tool's
structuredContent. This is that page.

Claude renders the image content block itself, so get_file and view_file keep
returning it; the page is additive, not a replacement.

The HTML lives in a .py module rather than an .html file because apply.sh
packages only app/*.py into the Lambda.
"""

import os
from urllib.parse import urlparse

URI = "ui://microvm/file-viewer.html"
MIME = "text/html;profile=mcp-app"

# One page, three kinds of payload, all from structuredContent:
#   image  {kind, name, mime, bytes, url}  url is a /view link. The server no
#          longer sends a data: URI -- see handler.run_tool -- but src is still
#          honoured if present, tried before url.
#   text   {kind, name, text}
#   link   {kind, name, url}
#
# Data arrives two ways, and the page listens for both: the MCP Apps bridge
# (a JSON-RPC ui/notifications/tool-result over postMessage, after a
# ui/initialize handshake), and ChatGPT's older window.openai.toolOutput.
HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; font: 14px system-ui, sans-serif; }
  .wrap { padding: 8px; }
  /* Pixels, not vh. Inside a host's frame vh is a share of the FRAME, and the
     frame starts small -- 70vh drew a 50px thumbnail in ChatGPT. 480px keeps
     a large square render readable without taking over the conversation;
     the image keeps its own proportions and a small one is not blown up. */
  img { display: block; max-width: 100%; max-height: 480px; width: auto;
        height: auto; margin: 0 auto; border-radius: 6px; }
  pre { margin: 0; max-height: 480px; overflow: auto; white-space: pre-wrap;
        font: 12px ui-monospace, Consolas, monospace; }
  .meta { margin-top: 6px; opacity: .7; font-size: 12px; text-align: center; }
  a { color: inherit; }
</style></head>
<body><div class="wrap" id="root"><div class="meta">Loading file...</div></div>
<script>
(function () {
  var root = document.getElementById("root");
  var drawn = false;

  // If no result ever arrives, say so rather than showing "Loading file..."
  // indefinitely -- a silent spinner is indistinguishable from a slow load.
  setTimeout(function () {
    if (!drawn) {
      root.textContent = "";
      var note = document.createElement("div");
      note.className = "meta";
      note.textContent = "The file was not received. Ask for it again.";
      root.appendChild(note);
      notifySize();
    }
  }, 15000);

  function el(tag, text) {
    var e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function link(href, text) {
    var a = el("a", text); a.href = href; a.target = "_blank";
    a.rel = "noopener"; return a;
  }
  function size(n) {
    if (!n && n !== 0) return "";
    return n > 1048576 ? (n / 1048576).toFixed(1) + " MB"
         : n > 1024 ? Math.round(n / 1024) + " KB" : n + " bytes";
  }

  function render(data) {
    if (!data || drawn) return;
    if (data.structuredContent) data = data.structuredContent;
    if (!data.kind) return;
    drawn = true;
    root.textContent = "";
    var meta = el("div"); meta.className = "meta";
    meta.appendChild(document.createTextNode((data.name || "file") +
      (data.bytes ? " \\u00b7 " + size(data.bytes) : "")));

    if (data.kind === "image") {
      var img = el("img"); img.alt = data.name || "image";
      var sources = [data.src, data.url].filter(Boolean), i = 0;
      img.onerror = function () {
        i += 1;
        if (i < sources.length) { img.src = sources[i]; }
        else { img.replaceWith(el("div", "The image could not be loaded.")); }
      };
      // The frame is sized from what the page reports; before the image has
      // loaded that is almost nothing, so report again once it has.
      img.onload = notifySize;
      img.src = sources[0];
      root.appendChild(img);
    } else if (data.kind === "text") {
      root.appendChild(el("pre", data.text || ""));
    }
    if (data.url) {
      meta.appendChild(document.createTextNode(" \\u00b7 "));
      meta.appendChild(link(data.url, "Open"));
    }
    root.appendChild(meta);
    notifySize();
  }

  // --- MCP Apps bridge ---------------------------------------------------
  var nextId = 1;
  function send(msg) { window.parent.postMessage(msg, "*"); }
  function notifySize() {
    var h = Math.ceil(document.documentElement.scrollHeight);
    send({ jsonrpc: "2.0", method: "ui/notifications/size-changed",
           params: { height: h } });
    // ChatGPT's older API for the same thing.
    if (window.openai && window.openai.notifyIntrinsicHeight) {
      window.openai.notifyIntrinsicHeight(h);
    }
  }
  // Catch every later change too -- a slow image, a font, a wrapped caption.
  if (window.ResizeObserver) new ResizeObserver(notifySize).observe(document.body);
  window.addEventListener("message", function (event) {
    var m = event.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.method === "ui/notifications/tool-result") render(m.params);
    if (m.id === 1 && m.result) {
      send({ jsonrpc: "2.0", method: "ui/notifications/initialized" });
    }
  });
  send({ jsonrpc: "2.0", id: nextId++, method: "ui/initialize",
         params: { appInfo: { name: "microvm-file-viewer", version: "1.0.0" },
                   appCapabilities: {}, protocolVersion: "2025-06-18" } });

  // --- ChatGPT's older window.openai globals -----------------------------
  function fromOpenAI() {
    if (window.openai && window.openai.toolOutput) render(window.openai.toolOutput);
  }
  window.addEventListener("openai:set_globals", fromOpenAI);
  fromOpenAI();
})();
</script></body></html>"""


def resource_domains():
    """Return the origins the page may load images from.

    The /view link is on this API, and it redirects to a presigned URL on the
    share bucket, so both have to be allowed -- the browser checks the
    redirect target against the CSP as well as the first URL. Both of S3's
    host styles are listed because which one boto3 signs for depends on the
    region and client configuration.
    """
    domains = []
    app = os.environ.get("APP_URL", "")
    if app:
        parsed = urlparse(app)
        domains.append(f"{parsed.scheme}://{parsed.netloc}")
    bucket = os.environ.get("SHARE_BUCKET", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if bucket:
        domains += [f"https://{bucket}.s3.amazonaws.com",
                    f"https://{bucket}.s3.{region}.amazonaws.com"]
    return domains


def resource():
    """Return the page as an MCP resource-contents entry, with its CSP."""
    domains = resource_domains()
    return {
        "uri": URI,
        "mimeType": MIME,
        "text": HTML,
        "_meta": {
            # The MCP Apps key, and ChatGPT's older equivalent, which uses a
            # different name and snake_case.
            "ui": {"csp": {"resourceDomains": domains, "connectDomains": []},
                   "prefersBorder": True},
            "openai/widgetCSP": {"resource_domains": domains,
                                 "connect_domains": []},
            "openai/widgetPrefersBorder": True,
        },
    }


# Attached to a tool in tools/list to say "render my result with the viewer".
# Both keys, because ChatGPT still honours its original name.
TOOL_META = {"ui": {"resourceUri": URI}, "openai/outputTemplate": URI}
