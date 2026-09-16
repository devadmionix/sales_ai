app_name = "sales_ai"
app_title = "Sales AI"
app_publisher = "Admionix"
app_description = "AI powered sales application"
app_email = "admin@admionix.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "sales_ai",
# 		"logo": "/assets/sales_ai/logo.png",
# 		"title": "Sales AI",
# 		"route": "/sales_ai",
# 		"has_permission": "sales_ai.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "sales_ai.bundle.css"
# The graph renderer is a plain script rather than part of the bundle: the bundle is the
# chat panel, which only mounts when Sales AI is enabled, and the playbook forms have to
# draw themselves either way.
app_include_js = ["sales_ai.bundle.js", "/assets/sales_ai/js/graph.js"]

# include js, css files in header of web template
# web_include_css = "/assets/sales_ai/css/sales_ai.css"
# web_include_js = "/assets/sales_ai/js/sales_ai.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "sales_ai/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Tells the desk whether to mount the assistant panel.
extend_bootinfo = "sales_ai.boot.extend_bootinfo"

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "sales_ai/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "sales_ai.utils.jinja_methods",
# 	"filters": "sales_ai.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "sales_ai.install.before_install"
# after_install = "sales_ai.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "sales_ai.uninstall.before_uninstall"
# after_uninstall = "sales_ai.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "sales_ai.utils.before_app_install"
# after_app_install = "sales_ai.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "sales_ai.utils.before_app_uninstall"
# after_app_uninstall = "sales_ai.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "sales_ai.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "sales_ai.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["sales_ai.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# Sales AI Triggers watch every doctype. `dispatch` returns on a single cache read unless
# a trigger has actually been configured for that doctype and event.
doc_events = {
	"*": {
		"after_insert": "sales_ai.triggers.dispatch",
		"on_update": "sales_ai.triggers.dispatch",
		"on_submit": "sales_ai.triggers.dispatch",
		"on_cancel": "sales_ai.triggers.dispatch",
	}
}

# Scheduled Tasks
# ---------------

# Five minutes is the finest a scheduled trigger can resolve, and is also how long a
# missed tick can delay one — or delay a waiting playbook waking up.
scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"sales_ai.triggers.run_scheduled",
			"sales_ai.playbook.resume_due",
		],
	},
	# Scores are re-derived overnight rather than on every order, because they are read
	# far more often than the rows underneath them change and a score that moves while a
	# manager is reading the list is worse than one that is a day old. `computed_on` says
	# exactly how old, which is why the field is there.
	"daily": [
		"sales_ai.intelligence.churn.recompute",
	],
}

# Testing
# -------

# before_tests = "sales_ai.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "sales_ai.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "sales_ai.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "sales_ai.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["sales_ai.utils.before_request"]
# after_request = ["sales_ai.utils.after_request"]

# Job Events
# ----------
# before_job = ["sales_ai.utils.before_job"]
# after_job = ["sales_ai.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"sales_ai.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

