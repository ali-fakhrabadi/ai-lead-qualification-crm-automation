"""CRM adapter for the HubSpot CRM v3 API shape.

Used for both providers:

- ``demo``: the bundled demo CRM service, which implements the subset of the API below so the
  whole workflow runs without an external account;
- ``hubspot``: a real HubSpot account (private app token). Not verified against a live account
  in this project; see docs/crm-integration.md.

Operations: search a contact by email, create, update, search/create a company by domain,
associate contact and company, add a note. Every HTTP attempt is reported to ``record`` so
retries are measurable. The retry loop and the transient/permanent classification live in
``app.crm.base`` and are shared with the other connectors.
"""

from __future__ import annotations

import re
from typing import Any

from app.crm.base import CrmError, HttpCrmClient, Recorder, RetryPolicy

__all__ = ["NOTE_TO_CONTACT", "CrmError", "HubSpotStyleClient", "Recorder", "RetryPolicy"]

NOTE_TO_CONTACT = 202  # HUBSPOT_DEFINED association type id: note -> contact


class HubSpotStyleClient(HttpCrmClient):
    # --- contacts ---------------------------------------------------------------------------
    def find_contact(self, email: str) -> str | None:
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
            ],
            "properties": ["email"],
            "limit": 1,
        }
        data = self._request(
            "search_contact", "POST", "/crm/v3/objects/contacts/search", body
        ).json()
        results = data.get("results") or []
        return str(results[0]["id"]) if results else None

    def create_contact(self, properties: dict[str, Any]) -> str:
        response = self._request(
            "create_contact",
            "POST",
            "/crm/v3/objects/contacts",
            {"properties": properties},
            accept=(409,),
        )
        if response.status_code == 409:
            # Created meanwhile (or search not yet consistent): HubSpot names the existing id.
            match = re.search(r"Existing ID:\s*(\d+)", response.text)
            if not match:
                raise CrmError(
                    "CRM reported a conflict without an id",
                    transient=False,
                    code="conflict",
                    http_status=409,
                )
            existing = match.group(1)
            self.update_contact(existing, properties)
            return existing
        return str(response.json()["id"])

    def update_contact(self, contact_id: str, properties: dict[str, Any]) -> None:
        self._request(
            "update_contact",
            "PATCH",
            f"/crm/v3/objects/contacts/{contact_id}",
            {"properties": properties},
        )

    # --- companies --------------------------------------------------------------------------
    def find_company(self, domain: str) -> str | None:
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]}
            ],
            "properties": ["domain"],
            "limit": 1,
        }
        data = self._request(
            "search_company", "POST", "/crm/v3/objects/companies/search", body
        ).json()
        results = data.get("results") or []
        return str(results[0]["id"]) if results else None

    def create_company(self, name: str, domain: str) -> str:
        data = self._request(
            "create_company",
            "POST",
            "/crm/v3/objects/companies",
            {"properties": {"name": name, "domain": domain}},
        ).json()
        return str(data["id"])

    def associate(self, contact_id: str, company_id: str) -> None:
        self._request(
            "associate",
            "PUT",
            f"/crm/v4/objects/contact/{contact_id}/associations/default/company/{company_id}",
        )

    def add_note(self, contact_id: str, body: str, timestamp_iso: str) -> str:
        payload = {
            "properties": {"hs_note_body": body, "hs_timestamp": timestamp_iso},
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": NOTE_TO_CONTACT,
                        }
                    ],
                }
            ],
        }
        data = self._request("create_note", "POST", "/crm/v3/objects/notes", payload).json()
        return str(data["id"])
