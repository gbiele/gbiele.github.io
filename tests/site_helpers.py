"""Helpers shared by the site tests (no dependency on the site's own code)."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = REPO_ROOT / "_site"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "legacy_site.json"

RESEARCH_SLUGS = ["causalinf", "bayes", "adhd", "epi", "decision-making"]
RESEARCH_ORDER = ["causalinf", "bayes", "adhd", "epi", "decision-making"]


def load_legacy():
    return json.loads(FIXTURE.read_text(encoding="utf8"))


def norm(text):
    """Contract normalisation: casefold, keep only Unicode letters and digits."""
    return "".join(c for c in text.casefold() if c.isalnum())


def collapse_ws(text):
    return " ".join(text.split())


def soup_of(path):
    return BeautifulSoup(Path(path).read_text(encoding="utf8", errors="replace"), "html.parser")


def page_text(path):
    soup = soup_of(path)
    body = soup.body or soup
    return collapse_ws(body.get_text(" "))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def is_internal(url):
    """Relative or root-relative URL (not http(s):, mailto:, #, data:, //host)."""
    url = url.strip()
    if not url or url.startswith("#") or url.startswith("//"):
        return False
    return not _SCHEME.match(url)


def resolve(site, page, url):
    """Resolve an internal URL found on `page` to a filesystem path under `site`.

    Strips ?query and #fragment; a path ending in '/' (or naming a directory)
    resolves to its index.html. Backslashes are treated as '/'.
    """
    url = url.strip().replace("\\", "/")
    url = url.split("#", 1)[0].split("?", 1)[0]
    url = unquote(url)
    if url.startswith("/"):
        target = os.path.join(str(site), url.lstrip("/"))
    else:
        target = os.path.join(str(Path(page).parent), url)
    target = Path(os.path.normpath(target))
    if url.endswith("/") or url == "" or target.is_dir():
        target = target / "index.html"
    return target


def urls_in_tag(tag):
    out = []
    for attr in ("href", "src"):
        val = tag.get(attr)
        if isinstance(val, str):
            out.append(val)
    srcset = tag.get("srcset")
    if isinstance(srcset, str):
        for part in srcset.split(","):
            part = part.strip()
            if part:
                out.append(part.split()[0])
    return out


def internal_urls(path):
    soup = soup_of(path)
    urls = []
    for tag in soup.find_all(True):
        urls.extend(u for u in urls_in_tag(tag) if is_internal(u))
    return urls


_META_URL = re.compile(r"url\s*=\s*['\"]?([^'\"]+)['\"]?", re.I)
_JS_PATTERNS = [
    re.compile(r"location(?:\.href)?\s*=\s*(['\"])(.+?)\1"),
    re.compile(r"location\.(?:replace|assign)\(\s*(['\"])(.+?)\1"),
]
# Quarto alias pages: `var redirects = {"": "..\\..\\research\\epi.html", ...}`
_JS_REDIRECT_MAP = re.compile(r"\bredirects\s*=\s*\{(.*?)\}", re.S)
_JS_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _js_unescape(s):
    try:
        return json.loads('"' + s + '"')
    except ValueError:
        return s


def redirect_targets(path):
    """Target URLs of meta refresh tags, window.location scripts and Quarto
    `redirects = {...}` maps (all values of the map)."""
    soup = soup_of(path)
    targets = []
    for meta in soup.find_all("meta"):
        if str(meta.get("http-equiv", "")).lower() == "refresh":
            m = _META_URL.search(str(meta.get("content", "")))
            if m:
                targets.append(m.group(1).strip())
    for script in soup.find_all("script"):
        code = script.string or script.get_text() or ""
        for pat in _JS_PATTERNS:
            targets.extend(m.group(2) for m in pat.finditer(code))
        for block in _JS_REDIRECT_MAP.finditer(code):
            targets.extend(_js_unescape(m.group(2)) for m in _JS_STRING.finditer(block.group(1)))
    return targets


def redirects_to(site, page, expected):
    """True if `page` redirects to the existing file `expected` (both under site)."""
    expected = Path(os.path.normpath(str(expected)))
    if not expected.is_file():
        return False
    for url in redirect_targets(page):
        if not is_internal(url.replace("\\", "/")):
            continue
        if resolve(site, page, url) == expected:
            return True
    return False


# --- minimal BibTeX reader (fields with balanced braces or quotes) ---------

_ENTRY = re.compile(r"@\s*([A-Za-z]+)\s*[{(]")


def _read_balanced(text, i, open_ch, close_ch):
    depth = 1
    j = i
    while j < len(text) and depth:
        if text[j] == open_ch:
            depth += 1
        elif text[j] == close_ch:
            depth -= 1
        j += 1
    return text[i:j - 1], j


def parse_bib(text):
    entries = []
    for m in _ENTRY.finditer(text):
        kind = m.group(1).lower()
        if kind in ("comment", "string", "preamble"):
            continue
        body, _ = _read_balanced(text, m.end(), "{", "}")
        fields = {}
        k = body.find(",")
        rest = body[k + 1:] if k >= 0 else ""
        pos = 0
        fm = re.compile(r"\s*([A-Za-z_\-]+)\s*=\s*")
        while pos < len(rest):
            mm = fm.match(rest, pos)
            if not mm:
                pos += 1
                continue
            name = mm.group(1).lower()
            p = mm.end()
            if p < len(rest) and rest[p] == "{":
                val, p = _read_balanced(rest, p + 1, "{", "}")
            elif p < len(rest) and rest[p] == '"':
                q = p + 1
                depth = 0
                while q < len(rest) and not (rest[q] == '"' and depth == 0):
                    depth += {"{": 1, "}": -1}.get(rest[q], 0)
                    q += 1
                val, p = rest[p + 1:q], q + 1
            else:
                vm = re.match(r"[^,]*", rest[p:])
                val, p = vm.group(0).strip(), p + vm.end()
            fields[name] = val
            nxt = rest.find(",", p)
            pos = len(rest) if nxt < 0 else nxt + 1
        entries.append({"type": kind, "fields": fields})
    return entries


def perfect_matching(left, right, ok):
    """Size of a maximum bipartite matching between `left` and `right`."""
    match_r = {}

    def try_assign(i, seen):
        for j in range(len(right)):
            if ok(left[i], right[j]) and j not in seen:
                seen.add(j)
                if j not in match_r or try_assign(match_r[j], seen):
                    match_r[j] = i
                    return True
        return False

    return sum(1 for i in range(len(left)) if try_assign(i, set()))


# --- Node.js client-side publication category filter harness ---------------

def _serialize_dom_node(tag):
    attrs = {}
    for k, v in tag.attrs.items():
        if isinstance(v, list):
            attrs[k] = " ".join(str(x) for x in v)
        elif v is True:
            attrs[k] = ""
        else:
            attrs[k] = str(v)
    children = []
    text_parts = []
    for child in tag.children:
        if isinstance(child, Tag):
            if child.name in ("script", "style"):
                continue
            children.append(_serialize_dom_node(child))
        else:
            s = str(child)
            if s.strip():
                text_parts.append(s)
    return {
        "tag": tag.name.lower(),
        "attrs": attrs,
        "text": " ".join(text_parts),
        "children": children,
    }


_NODE_FILTER_HARNESS = r"""
const vm = require("vm");
const fs = require("fs");

const payload = JSON.parse(fs.readFileSync(0, "utf8"));

function camelToData(prop) {
  return "data-" + String(prop).replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
}

function dataToCamel(attr) {
  return attr.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

function parseSimpleSelector(sel) {
  sel = sel.trim();
  const out = { tag: null, id: null, classes: [], attrs: [], nots: [] };
  // Extract :not(...)
  sel = sel.replace(/:not\(([^()]+)\)/g, (_, inner) => {
    out.nots.push(parseSimpleSelector(inner));
    return "";
  });
  // Extract [attr...]
  sel = sel.replace(/\[([^\]]+)\]/g, (_, body) => {
    const m = body.match(/^\s*([a-zA-Z0-9_\-:]+)\s*(?:(~=|=)\s*(?:"([^"]*)"|'([^']*)'|([^\s\]]+)))?\s*$/);
    if (m) {
      out.attrs.push({
        name: m[1].toLowerCase(),
        op: m[2] || null,
        val: m[3] !== undefined ? m[3] : (m[4] !== undefined ? m[4] : (m[5] !== undefined ? m[5] : null)),
      });
    }
    return "";
  });
  // Extract #id
  sel = sel.replace(/#([a-zA-Z0-9_\-]+)/g, (_, id) => {
    out.id = id;
    return "";
  });
  // Extract .class
  sel = sel.replace(/\.([a-zA-Z0-9_\-]+)/g, (_, cls) => {
    out.classes.push(cls);
    return "";
  });
  // Remaining tag
  sel = sel.trim();
  if (sel && sel !== "*") {
    out.tag = sel.toLowerCase();
  }
  return out;
}

function matchSimple(el, parsed) {
  if (parsed.tag && el._tag !== parsed.tag) return false;
  if (parsed.id && el.id !== parsed.id) return false;
  for (const cls of parsed.classes) {
    if (!el.classList.contains(cls)) return false;
  }
  for (const a of parsed.attrs) {
    if (!el.hasAttribute(a.name)) return false;
    if (a.op === "=") {
      if (el.getAttribute(a.name) !== a.val) return false;
    } else if (a.op === "~=") {
      const toks = (el.getAttribute(a.name) || "").trim().split(/\s+/).filter(Boolean);
      if (!toks.includes(a.val)) return false;
    }
  }
  for (const neg of parsed.nots) {
    if (matchSimple(el, neg)) return false;
  }
  return true;
}

function matchCompound(el, selector) {
  // Split selector into parts with combinators (' ' or '>')
  const tokens = [];
  let i = 0;
  const s = selector.trim();
  let buf = "";
  let bracket = 0;
  let paren = 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === "[") bracket++;
    else if (ch === "]") bracket--;
    else if (ch === "(") paren++;
    else if (ch === ")") paren--;
    if (bracket === 0 && paren === 0 && (ch === ">" || /\s/.test(ch))) {
      if (buf.trim()) {
        tokens.push({ type: "sel", value: parseSimpleSelector(buf.trim()) });
        buf = "";
      }
      if (ch === ">") {
        if (tokens.length && tokens[tokens.length - 1].type === "comb") {
          tokens[tokens.length - 1].value = ">";
        } else {
          tokens.push({ type: "comb", value: ">" });
        }
      } else if (tokens.length && tokens[tokens.length - 1].type !== "comb") {
        tokens.push({ type: "comb", value: " " });
      }
      i++;
      continue;
    }
    buf += ch;
    i++;
  }
  if (buf.trim()) tokens.push({ type: "sel", value: parseSimpleSelector(buf.trim()) });
  if (!tokens.length) return false;

  function check(node, idx) {
    const part = tokens[idx];
    if (!node || !matchSimple(node, part.value)) return false;
    if (idx === 0) return true;
    const comb = tokens[idx - 1].value;
    if (comb === ">") {
      return check(node.parentElement, idx - 2);
    }
    let anc = node.parentElement;
    while (anc) {
      if (check(anc, idx - 2)) return true;
      anc = anc.parentElement;
    }
    return false;
  }
  return check(el, tokens.length - 1);
}

function matchesSelector(el, selectorList) {
  return selectorList.split(",").some((part) => matchCompound(el, part));
}

class Element {
  constructor(node, parent) {
    this._tag = node.tag.toLowerCase();
    this.tagName = node.tag.toUpperCase();
    this.nodeName = this.tagName;
    this.nodeType = 1;
    this._attrs = Object.assign({}, node.attrs || {});
    this._text = node.text || "";
    this.parentNode = parent || null;
    this.parentElement = parent || null;
    this._listeners = {};
    this.style = { display: "" };
    if (this._attrs.style) {
      const m = this._attrs.style.match(/display\s*:\s*([^;]+)/i);
      if (m) this.style.display = m[1].trim();
    }
    this._hidden = Object.prototype.hasOwnProperty.call(this._attrs, "hidden");
    const self = this;
    this.classList = {
      _set() {
        return new Set((self._attrs.class || "").trim().split(/\s+/).filter(Boolean));
      },
      _save(set) {
        self._attrs.class = Array.from(set).join(" ");
      },
      contains(c) { return this._set().has(c); },
      add(...cs) { const s = this._set(); cs.forEach((c) => c && s.add(c)); this._save(s); },
      remove(...cs) { const s = this._set(); cs.forEach((c) => s.delete(c)); this._save(s); },
      toggle(c, force) {
        const s = this._set();
        const want = force !== undefined ? Boolean(force) : !s.has(c);
        if (want) s.add(c); else s.delete(c);
        this._save(s);
        return want;
      },
      toString() { return self._attrs.class || ""; },
    };
    this.dataset = new Proxy({}, {
      get(_, prop) {
        if (typeof prop !== "string") return undefined;
        const k = camelToData(prop);
        return Object.prototype.hasOwnProperty.call(self._attrs, k) ? self._attrs[k] : undefined;
      },
      set(_, prop, val) {
        self._attrs[camelToData(prop)] = String(val);
        return true;
      },
      has(_, prop) {
        return Object.prototype.hasOwnProperty.call(self._attrs, camelToData(prop));
      },
      ownKeys() {
        return Object.keys(self._attrs).filter((k) => k.startsWith("data-")).map(dataToCamel);
      },
    });
    this.children = (node.children || []).map((c) => new Element(c, this));
    this.childNodes = this.children;
  }
  get id() { return this._attrs.id || ""; }
  set id(v) { this._attrs.id = String(v); }
  get className() { return this._attrs.class || ""; }
  set className(v) { this._attrs.class = String(v); }
  get hidden() { return this._hidden; }
  set hidden(v) {
    this._hidden = Boolean(v);
    if (this._hidden) this._attrs.hidden = "";
    else delete this._attrs.hidden;
  }
  get textContent() {
    return [this._text, ...this.children.map((c) => c.textContent)].filter(Boolean).join(" ");
  }
  set textContent(v) { this._text = String(v); }
  get innerText() { return this.textContent; }
  set innerText(v) { this._text = String(v); }
  getAttribute(k) {
    k = String(k).toLowerCase();
    return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
  }
  setAttribute(k, v) {
    k = String(k).toLowerCase();
    this._attrs[k] = String(v);
    if (k === "hidden") this._hidden = true;
  }
  removeAttribute(k) {
    k = String(k).toLowerCase();
    delete this._attrs[k];
    if (k === "hidden") this._hidden = false;
  }
  hasAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this._attrs, String(k).toLowerCase());
  }
  matches(sel) { return matchesSelector(this, sel); }
  closest(sel) {
    let cur = this;
    while (cur) {
      if (cur.matches(sel)) return cur;
      cur = cur.parentElement;
    }
    return null;
  }
  _walk(fn) {
    for (const c of this.children) {
      fn(c);
      c._walk(fn);
    }
  }
  querySelectorAll(sel) {
    const res = [];
    this._walk((el) => { if (el.matches(sel)) res.push(el); });
    return res;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  addEventListener(type, fn) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  }
  removeEventListener(type, fn) {
    if (!this._listeners[type]) return;
    this._listeners[type] = this._listeners[type].filter((f) => f !== fn);
  }
  dispatchEvent(evt) {
    const e = Object.assign({
      type: "", target: this, currentTarget: this,
      preventDefault() {}, stopPropagation() {},
    }, evt);
    let cur = this;
    while (cur) {
      e.currentTarget = cur;
      const list = cur._listeners[e.type] || [];
      for (const fn of list) fn.call(cur, e);
      cur = cur.parentElement;
    }
  }
  click() { this.dispatchEvent({ type: "click" }); }
}

const root = new Element(payload.dom, null);
const docListeners = {};
const winListeners = {};

const locationObj = {
  search: payload.query || "",
  pathname: "/publications.html",
  hash: "",
  origin: "https://gbiele.github.io",
  get href() { return this.origin + this.pathname + this.search + this.hash; },
};

const documentObj = {
  readyState: "loading",
  body: root,
  documentElement: root,
  location: locationObj,
  querySelector: (sel) => root.querySelector(sel),
  querySelectorAll: (sel) => root.querySelectorAll(sel),
  getElementById: (id) => root.querySelector("#" + id),
  addEventListener(type, fn) {
    if (!docListeners[type]) docListeners[type] = [];
    docListeners[type].push(fn);
  },
  removeEventListener(type, fn) {
    if (!docListeners[type]) return;
    docListeners[type] = docListeners[type].filter((f) => f !== fn);
  },
};

const historyObj = {
  replaceState(_s, _t, url) {
    if (typeof url === "string") {
      const qIdx = url.indexOf("?");
      locationObj.search = qIdx >= 0 ? url.slice(qIdx).split("#")[0] : "";
    }
  },
  pushState(_s, _t, url) { this.replaceState(_s, _t, url); },
};

const windowObj = {
  document: documentObj,
  location: locationObj,
  history: historyObj,
  URL,
  URLSearchParams,
  console,
  addEventListener(type, fn) {
    if (!winListeners[type]) winListeners[type] = [];
    winListeners[type].push(fn);
  },
  removeEventListener() {},
};
windowObj.window = windowObj;
windowObj.self = windowObj;
windowObj.globalThis = windowObj;

const ctx = vm.createContext(windowObj);
for (const code of payload.scripts) {
  vm.runInContext(code, ctx, { timeout: 5000 });
}

documentObj.readyState = "complete";
for (const fn of (docListeners["DOMContentLoaded"] || [])) fn.call(documentObj, { type: "DOMContentLoaded" });
for (const fn of (winListeners["load"] || [])) fn.call(windowObj, { type: "load" });

function isVisible(el) {
  let cur = el;
  while (cur) {
    if (cur.hidden) return false;
    if (cur.style && String(cur.style.display).trim().toLowerCase() === "none") return false;
    if (
      cur.classList.contains("d-none") ||
      cur.classList.contains("hidden") ||
      cur.classList.contains("is-hidden")
    ) return false;
    cur = cur.parentElement;
  }
  return true;
}

function snapshot() {
  const buttons = root.querySelectorAll("button.pub-filter-btn").map((b) => ({
    category: b.getAttribute("data-category"),
    ariaPressed: b.getAttribute("aria-pressed"),
    activeClass: b.classList.contains("active"),
  }));
  const entries = root.querySelectorAll("article.pub-entry").map((e) => ({
    key: e.getAttribute("data-key"),
    categories: e.getAttribute("data-categories") || "",
    visible: isVisible(e),
  }));
  const years = root.querySelectorAll("section.pub-year").map((s) => {
    const parentPub = s.closest("#published, #preprints");
    return {
      year: s.getAttribute("data-year"),
      parentId: parentPub ? parentPub.id : null,
      visible: isVisible(s),
    };
  });
  const pubSec = root.querySelector("#published-section");
  const preSec = root.querySelector("#preprints-section");
  return {
    search: locationObj.search,
    buttons,
    entries,
    years,
    publishedSectionVisible: pubSec ? isVisible(pubSec) : null,
    preprintsSectionVisible: preSec ? isVisible(preSec) : null,
  };
}

const results = { initial: snapshot(), actions: {} };
for (const act of (payload.actions || [])) {
  if (act.type === "click") {
    const btn = root.querySelector(`button.pub-filter-btn[data-category="${act.category}"]`);
    if (!btn) throw new Error(`Button not found for category: ${act.category}`);
    btn.click();
    results.actions[act.label || act.category] = snapshot();
  }
}

process.stdout.write(JSON.stringify(results));
"""


def run_publications_filter_js(html_path, query="", extra_entries=None, actions=None):
    """Execute the inline category-filter script from publications.html in Node.js."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node executable not found on PATH")

    soup = soup_of(html_path)
    scripts = []
    for s in soup.find_all("script"):
        if s.get("src"):
            continue
        code = s.string or s.get_text() or ""
        if any(tok in code for tok in ("pub-filter-btn", "data-categories", "pub-category-filter")):
            scripts.append(code)
    if not scripts:
        return {"found": False}

    root = soup.body or soup
    dom = _serialize_dom_node(root)

    if extra_entries:
        # Inject synthetic article.pub-entry nodes into the first section.pub-year under #published
        def _find_first_pub_year(node):
            if node["tag"] == "section" and "pub-year" in node["attrs"].get("class", "").split():
                return node
            for child in node["children"]:
                found = _find_first_pub_year(child)
                if found is not None:
                    return found
            return None

        def _inject(node):
            if node["attrs"].get("id") == "published":
                target = _find_first_pub_year(node)
                if target is None:
                    return False
                for extra in extra_entries:
                    target["children"].append({
                        "tag": "article",
                        "attrs": {
                            "class": "pub-entry",
                            "data-key": extra["key"],
                            "data-year": target["attrs"].get("data-year", "2026"),
                            "data-pub-type": "article",
                            "data-categories": extra["categories"],
                        },
                        "text": extra.get("title", extra["key"]),
                        "children": [],
                    })
                return True
            return any(_inject(c) for c in node["children"])

        if not _inject(dom):
            raise AssertionError(
                "Could not find <section class='pub-year'> under #published to inject extra_entries"
            )

    payload = json.dumps({
        "dom": dom,
        "scripts": scripts,
        "query": query,
        "actions": actions or [],
    })
    proc = subprocess.run(
        [node, "-e", _NODE_FILTER_HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Node.js filter harness failed (exit {proc.returncode}):\n"
            f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
        )
    data = json.loads(proc.stdout)
    data["found"] = True
    return data
