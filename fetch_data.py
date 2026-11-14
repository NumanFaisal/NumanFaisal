#!/usr/bin/env python3
"""Refresh data/contributions.json (stdlib only). Needs GH_TOKEN in the environment."""
import json, os, sys, urllib.request
from pathlib import Path

USER = "NumanFaisal"
REPOS = ["NumanFaisal/Mystery-Message", "NumanFaisal/medium"]  # keep in sync with FEATURED
OUT = Path(__file__).resolve().parent.parent / "data" / "contributions.json"

def gql(query, **variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {os.environ['GH_TOKEN']}", "Content-Type": "application/json"},
    )
    out = json.load(urllib.request.urlopen(req, timeout=30))
    if out.get("errors"):
        sys.exit(f"GraphQL error: {out['errors']}")
    return out["data"]

cal = gql("""query($u:String!){user(login:$u){contributionsCollection{contributionCalendar{
  totalContributions weeks{contributionDays{date contributionCount}}}}}}""", u=USER
)["user"]["contributionsCollection"]["contributionCalendar"]

repos = {}
for slug in REPOS:
    owner, name = slug.split("/")
    try:
        r = gql("""query($o:String!,$n:String!){repository(owner:$o,name:$n){description isPrivate
          stargazerCount forkCount primaryLanguage{name} repositoryTopics(first:3){nodes{topic{name}}}}}""",
                o=owner, n=name)["repository"]
    except SystemExit:
        continue  # private / renamed: the card falls back to FEATURED in render_cards.py
    repos[slug] = dict(description=r["description"], private=r["isPrivate"], stars=r["stargazerCount"],
                       forks=r["forkCount"], language=(r["primaryLanguage"] or {}).get("name"),
                       topics=[n["topic"]["name"] for n in r["repositoryTopics"]["nodes"]])

OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps(dict(
    user=USER, total=cal["totalContributions"],
    days=[dict(date=d["date"], count=d["contributionCount"]) for w in cal["weeks"] for d in w["contributionDays"]],
    repos=repos), indent=1) + "\n")
print(f"wrote {OUT} ({cal['totalContributions']} contributions)")
