import frappe
from frappe.model.document import Document


class BSAPILog(Document):
    def on_update(self):
        if not self.is_new():
            frappe.throw("BS API Log is append-only")

    def on_trash(self):
        frappe.throw("BS API Log cannot be deleted")

