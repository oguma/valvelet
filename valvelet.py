#!/usr/bin/env python3
"""Valvelet - runway calculator. When does the money run out?"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane
from textual_plotext import PlotextPlot

APP_DIR = Path(__file__).parent
DAT_DIR = APP_DIR / "dat"

MAX_DAYS_DEFAULT = 36500
CHART_FALLBACK_DAYS = 3650
DAYS_PER_MONTH = 30.0
DAYS_PER_WEEK = 7.0

SCENARIO_COLORS = ["yellow", "magenta", "green", "red"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IncomeEntry:
    source: str
    amount: float
    frequency: str
    start: date
    end: date | None = None


@dataclass
class Activity:
    name: str
    cost: float
    days_per_week: float


@dataclass
class Scenario:
    name: str
    activities: list[Activity] = field(default_factory=list)


@dataclass
class SimResult:
    name: str
    dates: list[date]
    balances: list[float]
    death_day: date | None
    daily_burn: float
    monthly_burn: float
    daily_income_avg: float

    def truncated(self, length: int) -> SimResult:
        """Return a copy with dates/balances truncated to length."""
        return SimResult(
            name=self.name,
            dates=self.dates[:length],
            balances=self.balances[:length],
            death_day=self.death_day,
            daily_burn=self.daily_burn,
            monthly_burn=self.monthly_burn,
            daily_income_avg=self.daily_income_avg,
        )


# ---------------------------------------------------------------------------
# XML loading
# ---------------------------------------------------------------------------

def load_balance(path: Path) -> tuple[float, date]:
    tree = ET.parse(path)
    el = tree.getroot().find("current")
    amount = float(el.text)
    as_of = date.fromisoformat(el.get("as-of"))
    return amount, as_of


def load_fixed_costs(path: Path) -> float:
    """Return total monthly fixed costs."""
    tree = ET.parse(path)
    total = 0.0
    for cost in tree.getroot().findall("cost"):
        total += float(cost.find("amount").text)
    return total


def load_income(path: Path) -> list[IncomeEntry]:
    tree = ET.parse(path)
    entries = []
    for entry in tree.getroot().findall("entry"):
        to_el = entry.find("to")
        entries.append(IncomeEntry(
            source=entry.find("source").text,
            amount=float(entry.find("amount").text),
            frequency=entry.get("frequency"),
            start=date.fromisoformat(entry.find("from").text),
            end=date.fromisoformat(to_el.text) if to_el is not None else None,
        ))
    return entries


def load_scenarios(path: Path) -> list[Scenario]:
    tree = ET.parse(path)
    scenarios = []
    for scn in tree.getroot().findall("scenario"):
        activities = []
        for act in scn.findall("activity"):
            activities.append(Activity(
                name=act.find("name").text,
                cost=float(act.find("cost").text),
                days_per_week=float(act.find("days-per-week").text),
            ))
        scenarios.append(Scenario(
            name=scn.find("name").text,
            activities=activities,
        ))
    return scenarios


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def daily_income(incomes: list[IncomeEntry], d: date) -> float:
    """Calculate income for a given date."""
    total = 0.0
    for inc in incomes:
        if d < inc.start:
            continue
        if inc.end is not None and d > inc.end:
            continue
        if inc.frequency == "daily":
            total += inc.amount
        elif inc.frequency == "monthly":
            total += inc.amount / DAYS_PER_MONTH
        elif inc.frequency == "weekly":
            total += inc.amount / DAYS_PER_WEEK
        elif inc.frequency == "once":
            if d == inc.start:
                total += inc.amount
    return total


def daily_scenario_cost(scenario: Scenario) -> float:
    """Expected daily cost for a scenario."""
    total = 0.0
    for act in scenario.activities:
        total += act.cost * act.days_per_week / DAYS_PER_WEEK
    return total


def simulate(
    balance: float,
    start: date,
    incomes: list[IncomeEntry],
    fixed_monthly: float,
    scenario: Scenario,
    max_days: int = MAX_DAYS_DEFAULT,
) -> SimResult:
    """Simulate balance over time. max_days is a safety cap (default ~100 years)."""
    daily_fixed = fixed_monthly / DAYS_PER_MONTH
    daily_var = daily_scenario_cost(scenario)
    cash = balance

    dates: list[date] = []
    balances: list[float] = []
    death_day = None
    total_income = 0.0

    for i in range(max_days):
        d = start + timedelta(days=i)
        dates.append(d)
        balances.append(cash)

        if cash <= 0 and death_day is None:
            death_day = d
            break

        inc = daily_income(incomes, d)
        total_income += inc
        cash += inc - daily_fixed - daily_var
        if cash < 0:
            cash = 0.0

    sim_days = len(dates)
    return SimResult(
        name=scenario.name,
        dates=dates,
        balances=balances,
        death_day=death_day,
        daily_burn=daily_fixed + daily_var,
        monthly_burn=(daily_fixed + daily_var) * DAYS_PER_MONTH,
        daily_income_avg=total_income / sim_days if sim_days > 0 else 0.0,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_death_info(death_day: date | None, start: date) -> str:
    """Format death day as 'YYYY-MM-DD  (N days / M.M months)' or 'Survives'."""
    if death_day is None:
        return "Survives"
    days_left = (death_day - start).days
    months_left = days_left / DAYS_PER_MONTH
    return f"{death_day}  ({days_left} days / {months_left:.1f} months)"


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

class Valvelet(App):
    CSS_PATH = "styles.tcss"
    TITLE = "Valvelet"
    SUB_TITLE = "runway calculator"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent("Chart", "Death Day", "Metrics", id="tabs"):
            with TabPane("Chart", id="tab-chart"):
                yield PlotextPlot(id="chart")
            with TabPane("Death Day", id="tab-death"):
                yield Static("Loading death days...", id="death-days")
            with TabPane("Metrics", id="tab-metrics"):
                yield Static("Loading metrics...", id="metrics")
        yield Footer()

    def on_mount(self) -> None:
        self.set_timer(0.3, self.run_simulation)

    def on_resize(self) -> None:
        self.call_after_refresh(self.run_simulation)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self.call_after_refresh(self.run_simulation)

    def action_reload(self) -> None:
        self.run_simulation()
        self.notify("Reloaded XML data")

    # -- data loading -------------------------------------------------------

    def _load_data(self) -> tuple[float, date, float, list[IncomeEntry], list[Scenario]]:
        try:
            balance, start = load_balance(DAT_DIR / "balance.xml")
            fixed = load_fixed_costs(DAT_DIR / "fixed_costs.xml")
            incomes = load_income(DAT_DIR / "income.xml")
            scenarios = load_scenarios(DAT_DIR / "scenarios.xml")
        except (ET.ParseError, FileNotFoundError, ValueError) as e:
            self.notify(f"Error loading XML: {e}", severity="error")
            raise
        return balance, start, fixed, incomes, scenarios

    # -- simulation ---------------------------------------------------------

    def _run_all_scenarios(
        self,
        balance: float,
        start: date,
        incomes: list[IncomeEntry],
        fixed: float,
        scenarios: list[Scenario],
    ) -> list[SimResult]:
        """Run all scenarios, truncate to chart length, return sorted by death day."""
        results = [simulate(balance, start, incomes, fixed, scn) for scn in scenarios]

        # Determine chart length from dying scenarios
        death_lens = [len(r.balances) for r in results if r.death_day]
        chart_len = max(death_lens) if death_lens else CHART_FALLBACK_DAYS

        # Truncate (no re-simulation needed)
        results = [r.truncated(chart_len) for r in results]

        # Sort by earliest death first, survivors last
        results.sort(key=lambda r: r.death_day or date.max)
        return results

    # -- tab rendering ------------------------------------------------------

    def _update_chart(self, results: list[SimResult], balance: float, start: date) -> None:
        chart_widget = self.query_one("#chart", PlotextPlot)
        p = chart_widget.plt
        p.clear_figure()
        p.theme("dark")
        p.title("Balance over time")
        p.xlabel("Date")
        p.ylabel("JPY")
        p.date_form("Y-m-d")

        # Plot each scenario line
        all_bals: list[int] = []
        color_map: dict[str, str] = {}
        for i, res in enumerate(results):
            date_strs = [d.strftime("%Y-%m-%d") for d in res.dates]
            bals = [int(b) for b in res.balances]
            all_bals.extend(bals)
            color = SCENARIO_COLORS[i % len(SCENARIO_COLORS)]
            color_map[res.name] = color
            p.plot(date_strs, bals, label=res.name, color=color)

        # Y-axis ticks rounded to thousands
        y_max = max(all_bals) if all_bals else 0
        step = max(1000, (y_max // 5 // 1000) * 1000)
        top = ((y_max // step) + 1) * step
        y_ticks = list(range(0, top + 1, step))
        p.yticks(y_ticks, [str(v) for v in y_ticks])

        # Death day markers (stagger labels vertically)
        death_results = [r for r in results if r.death_day]
        for rank, res in enumerate(death_results):
            days_left = (res.death_day - start).days
            dd_str = res.death_day.strftime("%Y-%m-%d")
            y_pos = balance * (0.8 - rank * 0.12)
            p.vline(dd_str, "cyan")
            p.text(f"{dd_str} ({days_left}d) ", dd_str, y_pos, color="cyan", alignment="right")

        chart_widget.refresh()

    def _update_death_days(self, results: list[SimResult], start: date) -> None:
        lines = ["DEATH DAY", "", f"  As of: {start}", ""]
        for res in results:
            lines.append(f"  {res.name}")
            lines.append(f"    {format_death_info(res.death_day, start)}")
            lines.append("")
        self.query_one("#death-days", Static).update("\n".join(lines))

    def _update_metrics(
        self,
        results: list[SimResult],
        start: date,
        balance: float,
        fixed: float,
    ) -> None:
        lines = [
            "METRICS", "",
            f"  As of: {start}",
            f"  Balance: {balance:,.0f} JPY",
            f"  Fixed costs: {fixed:,.0f} JPY/mo",
            "",
        ]
        for res in results:
            monthly_income = res.daily_income_avg * DAYS_PER_MONTH
            net_daily = res.daily_income_avg - res.daily_burn
            net_monthly = net_daily * DAYS_PER_MONTH
            lines.append(f"  --- {res.name} ---")
            lines.append(f"    Daily burn:      {res.daily_burn:>10,.0f} JPY")
            lines.append(f"    Monthly burn:    {res.monthly_burn:>10,.0f} JPY")
            lines.append(f"    Daily income:    {res.daily_income_avg:>10,.0f} JPY")
            lines.append(f"    Monthly income:  {monthly_income:>10,.0f} JPY")
            lines.append(f"    Net daily:       {net_daily:>10,.0f} JPY")
            lines.append(f"    Net monthly:     {net_monthly:>10,.0f} JPY")
            lines.append(f"    Death day:    {format_death_info(res.death_day, start)}")
            lines.append("")
        self.query_one("#metrics", Static).update("\n".join(lines))

    # -- orchestrator -------------------------------------------------------

    def run_simulation(self) -> None:
        try:
            balance, start, fixed, incomes, scenarios = self._load_data()
        except Exception:
            return
        results = self._run_all_scenarios(balance, start, incomes, fixed, scenarios)
        self._update_chart(results, balance, start)
        self._update_death_days(results, start)
        self._update_metrics(results, start, balance, fixed)


def main() -> None:
    Valvelet().run()


if __name__ == "__main__":
    main()
