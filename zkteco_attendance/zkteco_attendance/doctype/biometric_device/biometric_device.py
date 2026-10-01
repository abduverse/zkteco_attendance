"""
Biometric Device Controller
"""

import frappe
from frappe import _
from frappe.model.document import Document


class BiometricDevice(Document):

    def validate(self):
        self._validate_company()
        self._validate_ip()
        self._validate_port()
        self._prevent_duplicate_ip()

    def _validate_company(self):
        # Company is optional when 'Ignore Company Restriction' is enabled.
        if not self.ignore_company_restriction and not self.company:
            frappe.throw(
                _("Company is required unless 'Ignore Company Restriction' is enabled.")
            )

    def _validate_ip(self):
        import re
        ip = self.device_ip or ""
        # Accept IPv4 or hostname
        ipv4 = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
        hostname = re.compile(r"^[a-zA-Z0-9\-\.]+$")
        if not (ipv4.match(ip) or hostname.match(ip)):
            frappe.throw(_("Invalid IP address or hostname: {0}").format(ip))

    def _validate_port(self):
        if not (1 <= int(self.port or 0) <= 65535):
            frappe.throw(_("Port must be between 1 and 65535."))

    def _prevent_duplicate_ip(self):
        existing = frappe.db.exists(
            "Biometric Device",
            {"device_ip": self.device_ip, "port": self.port, "name": ["!=", self.name]}
        )
        if existing:
            frappe.throw(
                _("A device with IP {0}:{1} already exists: {2}").format(
                    self.device_ip, self.port, existing
                )
            )
