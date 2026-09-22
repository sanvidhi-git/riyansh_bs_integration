import frappe
from frappe.model.document import Document

from riyansh_bs_integration.core.errors import PermissionDenied
from riyansh_bs_integration.services.kyc_service import approve_kyc, reject_kyc


class BSDistributorOnboarding(Document):
    def validate(self):
        if self.is_new() or self.flags.kyc_service_update:
            return
        previous_status = frappe.db.get_value(self.doctype, self.name, "kyc_status")
        if previous_status != self.kyc_status:
            raise PermissionDenied("Use the KYC approve/reject action to change KYC status")


@frappe.whitelist(methods=["POST"])
def approve(name):
    return approve_kyc(name)


@frappe.whitelist(methods=["POST"])
def reject(name, reason_code, reason):
    return reject_kyc(name, reason_code, reason)
