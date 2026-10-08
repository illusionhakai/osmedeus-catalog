"""Fixed public engine endpoints for scan control; no internal database access."""
import re

import httpx

from .batch_models import RESERVED_PARAMS


class EngineError(RuntimeError):
    def __init__(self, message, *, auth=False, uncertain=False):
        super().__init__(message)
        self.auth, self.uncertain = auth, uncertain


class Engine:
    def __init__(self, origin, transport=None):
        self.origin, self.transport = origin.rstrip("/"), transport

    def request(self, authorization, method, path, *, params=None, body=None, submission=False):
        try:
            with httpx.Client(base_url=self.origin, timeout=20, follow_redirects=False,
                              trust_env=False, transport=self.transport) as client:
                response = client.request(method, "/osm/api" + path,
                    headers={"Authorization": authorization}, params=params, json=body)
        except httpx.HTTPError as exc:
            raise EngineError("Engine unavailable; submission outcome unknown" if submission else "Engine unavailable",
                              uncertain=submission) from exc
        if response.status_code in (401, 403):
            raise EngineError("Engine authentication expired; authenticate and resume", auth=True)
        expected = 202 if submission else 200
        if response.status_code != expected:
            raise EngineError(f"Engine returned HTTP {response.status_code}",
                              uncertain=submission and not 400 <= response.status_code < 500)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
        except ValueError as exc:
            raise EngineError("Unexpected engine response", uncertain=submission) from exc
        return result

    def workflows(self, authorization, search="", offset=0, limit=50):
        body = self.request(authorization, "GET", "/workflows", params={"search": search, "offset": offset, "limit": limit})
        if not isinstance(body.get("data"), list) or not isinstance(body.get("pagination"), dict):
            raise EngineError("Invalid workflow listing")
        if type(body["pagination"].get("total")) is not int or body["pagination"]["total"] < 0:
            raise EngineError("Invalid workflow pagination")
        if any(not isinstance(w, dict) or not isinstance(w.get("name"), str) or w.get("kind") not in ("flow", "module") for w in body["data"]):
            raise EngineError("Invalid workflow listing entries")
        return body

    def workflow(self, authorization, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("Invalid workflow name")
        body = self.request(authorization, "GET", "/workflows/" + name, params={"json": "true"})
        parameters = body.get("params") or []
        if body.get("name") != name or body.get("kind") not in ("flow", "module") or not isinstance(parameters, list):
            raise EngineError("Invalid workflow details")
        if any(not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"].strip() for p in parameters):
            raise EngineError("Invalid workflow parameters")
        return {"name": body["name"], "kind": body["kind"], "description": body.get("description", ""),
                "params": [p for p in parameters if p["name"].strip().lower() not in RESERVED_PARAMS]}

    def validate_mapping(self, authorization, mapping):
        details = self.workflow(authorization, mapping["name"])
        if details["kind"] != mapping["kind"]:
            raise ValueError("Workflow kind changed")
        declared = {p["name"]: p for p in details["params"]}
        if set(mapping["params"]) - set(declared):
            raise ValueError("Unknown workflow parameter")
        for name, parameter in declared.items():
            if parameter.get("required") and not parameter.get("generator") and not mapping["params"].get(name, parameter.get("default")):
                raise ValueError(f"Required workflow parameter: {name}")
        return details

    def organization(self, authorization, org_uuid):
        body = self.request(authorization, "GET", "/orgs/" + org_uuid)
        result = body.get("data", body)
        if not isinstance(result, dict) or result.get("uuid") != org_uuid:
            raise EngineError("Destination organization unavailable")
        return {"uuid": org_uuid, "name": result.get("name", org_uuid)}

    def submit(self, authorization, payload):
        result = self.request(authorization, "POST", "/runs", body=payload, submission=True)
        if (result.get("status") != "queued" or not isinstance(result.get("job_id"), str)
                or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", result["job_id"])
                or type(result.get("target_count")) is not int or result["target_count"] < 1):
            raise EngineError("Submission outcome unknown; inspect engine jobs", uncertain=True)
        return {key: result[key] for key in ("job_id", "target_count", "status")}

    def job(self, authorization, job_id):
        body = self.request(authorization, "GET", "/jobs/" + job_id)
        if body.get("run_group_id") != job_id or not isinstance(body.get("runs"), list) or not isinstance(body.get("progress"), dict):
            raise EngineError("Unexpected job status")
        runs = []
        for run in body["runs"]:
            if not isinstance(run, dict) or not isinstance(run.get("run_uuid"), str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", run["run_uuid"]):
                raise EngineError("Invalid run identifier")
            runs.append({key: run.get(key) for key in ("run_uuid", "target", "status", "completed_steps", "total_steps")})
        return runs

    def cancel(self, authorization, run_uuid):
        result = self.request(authorization, "DELETE", "/runs/" + run_uuid)
        if result.get("run_uuid") != run_uuid:
            raise EngineError("Cancellation response could not be confirmed; inspect engine run status")
        return result
