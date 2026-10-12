
import json, subprocess, urllib.error, urllib.request

nl = chr(10)
out = subprocess.run(["git", "credential", "fill"], input=("protocol=https" + nl + "host=github.com" + nl),
                     capture_output=True, text=True).stdout
token = ""
for line in out.splitlines():
    if line.startswith("password="):
        token = line.split("=", 1)[1]
HEADERS = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "dsh-agent"}
REPO = "Qinghe9/Travel-assistant"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


opener = urllib.request.build_opener(NoRedirect)


def api(path):
    req = urllib.request.Request("https://api.github.com" + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def job_log(job_id):
    url = "https://api.github.com/repos/" + REPO + "/actions/jobs/" + str(job_id) + "/logs"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with opener.open(req, timeout=60) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        loc = exc.headers.get("Location")
        if not loc:
            raise
        req2 = urllib.request.Request(loc, headers={"User-Agent": "dsh-agent"})
        with urllib.request.urlopen(req2, timeout=60) as r:
            return r.read().decode("utf-8", "replace")


runs = api("/repos/" + REPO + "/actions/runs?per_page=1")["workflow_runs"]
jobs = api("/repos/" + REPO + "/actions/runs/" + str(runs[0]["id"]) + "/jobs")["jobs"]
for j in jobs:
    if j["conclusion"] == "success":
        continue
    print("=" * 15, j["name"], "=" * 15)
    log = job_log(j["id"])
    keep = [l for l in log.splitlines() if l.strip() and "##[debug]" not in l]
    print(nl.join(keep[-40:]))
    print()
