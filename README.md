### Sales AI

AI powered sales application

### Screenshots

> Marketplace listing requires screenshots. Capture the desk at 1440px wide and
> place the PNGs in `screenshots/` (see `screenshots/README.md`), then upload the
> same files to the Frappe Cloud marketplace listing.

- Chat panel: `screenshots/01-chat-panel.png`
- Sales Dashboard: `screenshots/02-sales-dashboard.png`
- Playbook: `screenshots/03-playbook.png`
- Trigger: `screenshots/04-trigger.png`

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app sales_ai
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/sales_ai
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
