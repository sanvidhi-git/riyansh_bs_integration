frappe.ui.form.on("BS Distributor Onboarding", {
  refresh(frm) {
    if (frm.is_new() || !["Pending", "Under Review"].includes(frm.doc.kyc_status)) {
      return;
    }

    const roles = frappe.user_roles || [];
    if (!roles.includes("System Manager") && !roles.includes("Riyansh KYC Approver")) {
      return;
    }

    frm.add_custom_button(__("Approve KYC"), () => {
      frappe.confirm(
        __("Approve this KYC? The Supplier will be created and the PASS result will be sent to Business Software automatically when outbound integration is enabled."),
        () => {
          frappe.call({
            method: "riyansh_bs_integration.riyansh_bs_integration.doctype.bs_distributor_onboarding.bs_distributor_onboarding.approve",
            args: { name: frm.doc.name },
            freeze: true,
            freeze_message: __("Approving KYC..."),
          }).then(() => frm.reload_doc());
        }
      );
    }, __("KYC"));

    frm.add_custom_button(__("Reject KYC"), () => {
      frappe.prompt(
        [
          { fieldname: "reason_code", fieldtype: "Data", label: __("Failure Reason Code"), reqd: 1 },
          { fieldname: "reason", fieldtype: "Small Text", label: __("Failure Reason"), reqd: 1 },
        ],
        (values) => {
          frappe.call({
            method: "riyansh_bs_integration.riyansh_bs_integration.doctype.bs_distributor_onboarding.bs_distributor_onboarding.reject",
            args: { name: frm.doc.name, reason_code: values.reason_code, reason: values.reason },
            freeze: true,
            freeze_message: __("Rejecting KYC..."),
          }).then(() => frm.reload_doc());
        },
        __("Reject KYC"),
        __("Reject")
      );
    }, __("KYC"));
  },
});
