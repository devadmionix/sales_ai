frappe.pages["sales-dashboard"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Sales Dashboard"),
		single_column: true,
	});

	page.dashboard = new SalesDashboard(page);
};

frappe.pages["sales-dashboard"].on_page_show = function (wrapper) {
	wrapper.page.dashboard.refresh();
};

class SalesDashboard {
	constructor(page) {
		this.page = page;
		this.$body = $(page.body);
		this.$body.html(frappe.render_template("sales_dashboard"));
		this.setup_filters();
		this.refresh();
	}

	setup_filters() {
		const me = this;
		const $filters = this.$body.find(".sai-dash-filters");

		this.company_filter = this.page.add_field({
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => me.refresh(),
		});

		this.from_date_filter = this.page.add_field({
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			change: () => me.refresh(),
		});

		this.to_date_filter = this.page.add_field({
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			change: () => me.refresh(),
		});

		// Quick period buttons
		this.page.add_inner_button(__("This Month"), () => {
			me.from_date_filter.set_value(frappe.datetime.month_start());
			me.to_date_filter.set_value(frappe.datetime.month_end());
		});
		this.page.add_inner_button(__("This Quarter"), () => {
			me.from_date_filter.set_value(frappe.datetime.quarter_start());
			me.to_date_filter.set_value(frappe.datetime.quarter_end());
		});
		this.page.add_inner_button(__("This Year"), () => {
			me.from_date_filter.set_value(frappe.datetime.year_start());
			me.to_date_filter.set_value(frappe.datetime.year_end());
		});
	}

	refresh() {
		const company = this.company_filter?.get_value();
		const from_date = this.from_date_filter?.get_value();
		const to_date = this.to_date_filter?.get_value();

		if (!company || !from_date || !to_date) return;

		const $body = this.$body.find(".sai-dash-body");
		$body.html('<div class="sai-dash-loading text-muted text-center p-5">' + __("Loading...") + "</div>");

		frappe.call({
			method: "sales_ai.sales_ai.page.sales_dashboard.sales_dashboard.get_kpis",
			args: { company, from_date, to_date },
			callback: (r) => {
				if (r.message && r.message.error) {
					$body.html('<div class="text-muted text-center p-5">' + __(r.message.error) + "</div>");
					return;
				}
				this.render(r.message || {});
			},
		});
	}

	render(data) {
		const $body = this.$body.find(".sai-dash-body");
		const currency = frappe.boot.sysdefaults.currency || "INR";
		const fmt = (v) => format_currency(v, currency, 0);
		const rev = data.revenue || {};
		const target = data.target || {};
		const pipeline = data.pipeline || {};
		const wl = data.won_lost || {};
		const conv = data.conversion_rate || {};
		const aov = data.aov || {};
		const cycle = data.sales_cycle || {};

		const achievement = target.amount ? flt((rev.total / target.amount) * 100, 1) : 0;
		const achievementClass = achievement >= 100 ? "green" : achievement >= 75 ? "orange" : "red";

		let html = `<div class="sai-kpi-grid">`;

		// Row 1: Revenue, Target, Achievement
		html += this.kpi_card(__("Revenue"), fmt(rev.total), `${rev.count} ${__("orders")}`,
			"blue", "Sales Order", {"docstatus": 1});
		html += this.kpi_card(__("Target"), target.amount ? fmt(target.amount) : __("Not Set"),
			target.fiscal_year ? `FY ${target.fiscal_year}` : "", "cyan");
		html += this.kpi_card(__("Achievement"), target.amount ? `${achievement}%` : __("N/A"),
			target.amount ? `${fmt(rev.total)} / ${fmt(target.amount)}` : __("No target set"),
			achievementClass);

		// Row 2: Pipeline, Weighted Pipeline, AOV
		html += this.kpi_card(__("Pipeline"), fmt(pipeline.total),
			`${pipeline.count} ${__("open opportunities")}`, "purple",
			"Opportunity", {"status": ["not in", ["Lost", "Closed"]]});
		html += this.kpi_card(__("Weighted Pipeline"), fmt(pipeline.weighted),
			__("Amount × Probability"), "violet");
		html += this.kpi_card(__("Avg Order Value"), aov.order_count ? fmt(aov.value) : __("N/A"),
			aov.order_count ? `${aov.order_count} ${__("orders")}` : __("No orders"), "blue");

		// Row 3: Won, Lost, Conversion Rate
		html += this.kpi_card(__("Won"), `${wl.won_count}`, fmt(wl.won_amount), "green",
			"Opportunity", {"status": "Converted"});
		html += this.kpi_card(__("Lost"), `${wl.lost_count}`, fmt(wl.lost_amount), "red",
			"Opportunity", {"status": "Lost"});
		html += this.kpi_card(__("Conversion Rate"), `${conv.rate}%`,
			`${conv.converted} / ${conv.total} ${__("opportunities")}`,
			conv.rate >= 30 ? "green" : conv.rate >= 15 ? "orange" : "red");

		// Row 4: Sales Cycle
		html += this.kpi_card(__("Sales Cycle"), cycle.count ? `${cycle.median_days} ${__("days")}` : __("N/A"),
			cycle.count ? `${__("Median from")} ${cycle.count} ${__("deals")}` : __("No converted deals"),
			"cyan");

		html += `</div>`;

		// Top Items table
		const items = data.top_items || [];
		if (items.length) {
			html += `<div class="sai-section">
				<h5 class="sai-section-title">${__("Top Selling Items")}</h5>
				<table class="table table-sm sai-table">
					<thead><tr>
						<th>${__("Item")}</th>
						<th class="text-right">${__("Qty")}</th>
						<th class="text-right">${__("Revenue")}</th>
					</tr></thead><tbody>`;
			items.forEach((item) => {
				html += `<tr class="sai-table-row" data-doctype="Item" data-name="${encodeURIComponent(item.item_code)}">
					<td>${frappe.utils.escape_html(item.item_name || item.item_code)}</td>
					<td class="text-right">${flt(item.qty, 1)}</td>
					<td class="text-right">${fmt(item.revenue)}</td>
				</tr>`;
			});
			html += `</tbody></table></div>`;
		}

		// Monthly Trend
		const trend = data.monthly_trend || [];
		if (trend.length > 1) {
			html += `<div class="sai-section">
				<h5 class="sai-section-title">${__("Monthly Revenue Trend")}</h5>
				<div class="sai-chart" id="sai-trend-chart"></div>
			</div>`;
		}

		$body.html(html);

		// Drill-down click handlers
		$body.find("[data-drill-doctype]").on("click", function () {
			const dt = $(this).attr("data-drill-doctype");
			const filters = JSON.parse($(this).attr("data-drill-filters") || "{}");
			frappe.set_route("List", dt, filters);
		});

		$body.find(".sai-table-row").on("click", function () {
			const dt = $(this).data("doctype");
			const name = decodeURIComponent($(this).data("name"));
			frappe.set_route("Form", dt, name);
		});

		// Render chart
		if (trend.length > 1) {
			new frappe.Chart("#sai-trend-chart", {
				data: {
					labels: trend.map((t) => t.month),
					datasets: [{ name: __("Revenue"), values: trend.map((t) => t.revenue) }],
				},
				type: "bar",
				height: 250,
				colors: ["#2490EF"],
			});
		}
	}

	kpi_card(title, value, subtitle, color, drill_doctype, drill_filters) {
		const drill = drill_doctype
			? `data-drill-doctype="${drill_doctype}" data-drill-filters='${JSON.stringify(drill_filters || {})}' style="cursor:pointer"`
			: "";
		return `<div class="sai-kpi-card" ${drill}>
			<div class="sai-kpi-title">${title}</div>
			<div class="sai-kpi-value indicator-pill ${color}">${value}</div>
			<div class="sai-kpi-subtitle">${subtitle}</div>
		</div>`;
	}
}
