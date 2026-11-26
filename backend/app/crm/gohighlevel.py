"""CRM connector for GoHighLevel (LeadConnector API v2).

Request shapes follow GoHighLevel's published OpenAPI specs (``apps/contacts.json`` and
``apps/businesses.json`` in GoHighLevel/highlevel-api-docs). **Not verified against a real
GoHighLevel account in this project**: it is exercised against the local mock in
``tests/mocks/gohighlevel.py`` only. See docs/crm-integration.md.

Everything is scoped to one sub-account (``locationId``). Authentication is a Sub-Account
Private Integration Token (scopes ``contacts.readonly``, ``contacts.write``,
``businesses.readonly``, ``businesses.write``); every request carries ``Version: 2021-07-28``.

How the sync's operations map:

| operation      | endpoint                                                  |
|----------------|-----------------------------------------------------------|
| find_contact   | ``GET /contacts/search/duplicate?locationId=&email=``     |
| create_contact | ``POST /contacts/upsert`` (matches an existing contact)   |
| update_contact | ``PUT /contacts/{id}``                                    |
| find_company   | ``GET /businesses/?locationId=&limit=&skip=``, by website |
| create_company | ``POST /businesses/``                                     |
| associate      | ``POST /contacts/bulk/business``                          |
| add_note       | ``POST /contacts/{id}/notes``                             |

Creating through the upsert endpoint is what makes a concurrent create safe: GoHighLevel
matches the email against the location's existing contacts (as long as the location does not
allow duplicate contacts, the default) and updates that contact instead of adding a second one.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.crm.base import HttpCrmClient

API_VERSION = "2021-07-28"
DEFAULT_BASE_URL = "https://services.leadconnectorhq.com"
# Contact fields GoHighLevel accepts at the top level; any other mapped property is sent as a
# custom field by key.
CONTACT_FIELDS = frozenset(
    {
        "firstName",
        "lastName",
        "name",
        "email",
        "phone",
        "companyName",
        "website",
        "country",
        "source",
        "timezone",
        "address1",
        "city",
        "state",
        "postalCode",
    }
)
BUSINESS_PAGE = 100
BUSINESS_MAX_PAGES = 20


def _host(url: str | None) -> str:
    if not url:
        return ""
    host = urlsplit(url if "//" in url else f"//{url}").hostname or ""
    return host.lower().removeprefix("www.")


class GoHighLevelClient(HttpCrmClient):
    def __init__(self, base_url: str, token: str, *, location_id: str, **kwargs: Any):
        super().__init__(base_url, token, headers={"Version": API_VERSION}, **kwargs)
        self.location_id = location_id

    @staticmethod
    def _contact_body(properties: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {}
        custom: list[dict[str, Any]] = []
        for name, value in properties.items():
            if name in CONTACT_FIELDS:
                body[name] = value
            else:
                custom.append({"key": name, "field_value": value})
        if custom:
            body["customFields"] = custom
        return body

    # --- contacts ---------------------------------------------------------------------------
    def find_contact(self, email: str) -> str | None:
        data = self._request(
            "search_contact",
            "GET",
            "/contacts/search/duplicate",
            params={"locationId": self.location_id, "email": email},
        ).json()
        contact = data.get("contact") if isinstance(data, dict) else None
        if isinstance(contact, dict) and contact.get("id"):
            return str(contact["id"])
        return None

    def create_contact(self, properties: dict[str, Any]) -> str:
        body = {"locationId": self.location_id, **self._contact_body(properties)}
        data = self._request("upsert_contact", "POST", "/contacts/upsert", body).json()
        return self._field(data, "contact", "id")

    def update_contact(self, contact_id: str, properties: dict[str, Any]) -> None:
        self._request(
            "update_contact", "PUT", f"/contacts/{contact_id}", self._contact_body(properties)
        )

    # --- businesses -------------------------------------------------------------------------
    def find_company(self, domain: str) -> str | None:
        domain = domain.lower()
        for page in range(BUSINESS_MAX_PAGES):
            data = self._request(
                "search_company",
                "GET",
                "/businesses/",
                params={
                    "locationId": self.location_id,
                    "limit": str(BUSINESS_PAGE),
                    "skip": str(page * BUSINESS_PAGE),
                },
            ).json()
            businesses = data.get("businesses") or []
            for business in businesses:
                if _host(business.get("website")) == domain and business.get("id"):
                    return str(business["id"])
            if len(businesses) < BUSINESS_PAGE:
                return None
        return None

    def create_company(self, name: str, domain: str) -> str:
        data = self._request(
            "create_company",
            "POST",
            "/businesses/",
            {"name": name, "locationId": self.location_id, "website": f"https://{domain}"},
        ).json()
        # The published schema spells the key "buiseness"; accept the corrected spelling too.
        key = "buiseness" if isinstance(data.get("buiseness"), dict) else "business"
        return self._field(data, key, "id")

    def associate(self, contact_id: str, company_id: str) -> None:
        self._request(
            "associate",
            "POST",
            "/contacts/bulk/business",
            {"locationId": self.location_id, "ids": [contact_id], "businessId": company_id},
        )

    def add_note(self, contact_id: str, body: str, timestamp_iso: str) -> str:  # noqa: ARG002
        # GoHighLevel stamps the note itself (dateAdded); the timestamp is not sent.
        data = self._request(
            "create_note", "POST", f"/contacts/{contact_id}/notes", {"body": body}
        ).json()
        return self._field(data, "note", "id")
