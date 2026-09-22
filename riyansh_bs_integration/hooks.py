app_name = "riyansh_bs_integration"
app_title = "Riyansh BS Integration"
app_publisher = "Sanvidhi Tech"
app_description = "Four-flow Business Software integration for Riyansh"
app_email = ""
app_license = "MIT"
app_version = "0.1.0"

after_install = "riyansh_bs_integration.install.after_install"
after_migrate = "riyansh_bs_integration.install.after_migrate"

scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "riyansh_bs_integration.core.outbound.process_due_events",
        ],
    },
}

