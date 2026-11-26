#!/usr/bin/env python3
"""Rebuild the projects band at the bottom of README.md.

Reads the owner's public repositories from the GitHub API, groups them by
family topic (see .github/projects.json) and rewrites only the text between
the PROJECTS:START / PROJECTS:END markers. Everything else in README.md is
left untouched. Standard library only.

Usage: update_projects.py [--readme PATH] [--config PATH] [--stdout]
Env:   GITHUB_TOKEN (optional, raises the API rate limit)
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
START = "<!-- PROJECTS:START -->"
END = "<!-- PROJECTS:END -->"
SEMVER = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


def api(path, raw=False):
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github.raw" if raw else "application/vnd.github+json")
    req.add_header("User-Agent", "profile-projects-updater")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise
    return body if raw else json.loads(body)


def public_repos(owner):
    repos, page = [], 1
    while True:
        batch = api(f"/users/{owner}/repos?type=owner&per_page=100&page={page}")
        if not batch:
            return repos
        repos.extend(batch)
        page += 1


def version_of(owner, name):
    """Highest semver among the VERSION file and the tags, else None."""
    candidates = []
    text = api(f"/repos/{owner}/{name}/contents/VERSION", raw=True)
    if text and text.strip():
        candidates.append(text.strip().splitlines()[0].strip())
    candidates += [t["name"] for t in (api(f"/repos/{owner}/{name}/tags?per_page=100") or [])]
    best = None
    for c in candidates:
        m = SEMVER.match(c)
        if m:
            key = tuple(int(x or 0) for x in m.groups())
            if best is None or key > best[0]:
                best = (key, c.lstrip("v"))
    return best[1] if best else None


def cell(text):
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def describe(it, en):
    if it and en:
        return f"{cell(it)}<br><sub>{cell(en)}</sub>"
    return cell(it or en)


def render(cfg, repos):
    owner = cfg["owner"]
    families = cfg["families"]
    topics = {f["topic"] for f in families}
    groups = {f["topic"]: [] for f in families}
    groups["_other"] = []
    public_names = {r["name"].lower() for r in repos}

    for r in repos:
        text = cfg.get("repos", {}).get(r["name"], {})
        fam = next((t for t in r.get("topics", []) if t in topics), None)
        fam = fam or (text.get("family") if text.get("family") in topics else "_other")
        lic = (r.get("license") or {}).get("spdx_id")
        groups[fam].append({
            "label": text.get("label", r["name"]),
            "url": r["html_url"],
            "desc": describe(text.get("it"), text.get("en")) or cell(r.get("description")),
            "version": version_of(owner, r["name"]),
            "license": None if lic in (None, "NOASSERTION") else lic,
            "upcoming": False,
        })

    # Private repositories are invisible to the API token used by the Action,
    # so they are described in the config. Once public, they are listed above.
    for u in cfg.get("private", []):
        if u["name"].lower() in public_names:
            continue
        groups.setdefault(u.get("family", "_other"), []).append({
            "label": u.get("label", u["name"]),
            "url": None,
            "desc": describe(u.get("it"), u.get("en")),
            "version": None,
            "license": u.get("license"),
            "upcoming": u.get("status", "private"),
        })

    out = []
    sections = [(f["topic"], f["title"], f.get("intro", {})) for f in families]
    sections.append(("_other", cfg["other"]["title"], cfg["other"].get("intro", {})))
    for key, title, intro in sections:
        rank = {False: 0, "upcoming": 1, "private": 2}
        items = sorted(groups.get(key, []), key=lambda i: (rank.get(i["upcoming"], 2), i["label"].lower()))
        if not items:
            continue
        out.append(f"### {title['it']} · {title['en']}")
        out.append("")
        if intro.get("it") or intro.get("en"):
            out.append(describe(intro.get("it"), intro.get("en")))
            out.append("")
        out.append("| Progetto · Project | Descrizione · Description | Versione · Version | Licenza · License |")
        out.append("|---|---|---|---|")
        for i in items:
            name = f"[**{i['label']}**]({i['url']})" if i["url"] else f"**{i['label']}**"
            version = {
                "upcoming": "🚧 in arrivo · coming soon",
                "private": "🔒 privato · private",
            }.get(i["upcoming"], i["version"] or "—")
            out.append(f"| {name} | {i['desc']} | {version} | {i['license'] or '—'} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", default=os.path.join(root, "README.md"))
    ap.add_argument("--config", default=os.path.join(root, ".github", "projects.json"))
    ap.add_argument("--stdout", action="store_true", help="print the band, do not write")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    exclude = {n.lower() for n in cfg.get("exclude", [])}
    repos = [r for r in public_repos(cfg["owner"])
             if not r.get("fork") and not r.get("archived") and not r.get("private")
             and r["name"].lower() not in exclude]
    band = render(cfg, repos)

    if args.stdout:
        sys.stdout.write(band)
        return 0

    with open(args.readme, encoding="utf-8") as fh:
        readme = fh.read()
    if START not in readme or END not in readme:
        sys.exit(f"Markers {START} / {END} not found in {args.readme}")
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    new = f"{head}{START}\n{band}{END}{tail}"
    if new != readme:
        with open(args.readme, "w", encoding="utf-8") as fh:
            fh.write(new)
        print("README.md updated")
    else:
        print("README.md already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
