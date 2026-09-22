app_name = "riyansh_bs_integration"
app_title = "Riyansh BS Integration"
app_publisher = "Sanvidhi Tech"
app_description = "Four-flow Business Software integration for Riyansh"
app_email = ""
app_license = "MIT"
app_version = "0.1.0"
app_logo_url = "/assets/riyansh_bs_integration/images/logo.svg"
app_home = "/desk/riyansh-bs-integration"
required_apps = ["erpnext"]

add_to_apps_screen = [
    {
        "name": "riyansh_bs_integration",
        "logo": "/assets/riyansh_bs_integration/images/logo.svg",
        "title": "Riyansh BS Integration",
        "route": "/desk/riyansh-bs-integration",
        "has_permission": "riyansh_bs_integration.utils.permissions.has_app_permission",
    }
]

after_install = "riyansh_bs_integration.install.after_install"
after_migrate = "riyansh_bs_integration.install.after_migrate"

scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "riyansh_bs_integration.core.outbound.process_due_events",
        ],
    },
}

