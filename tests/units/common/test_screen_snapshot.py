import cv2
import numpy as np
import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.screen_snapshot import (
    Check,
    Diff,
    RefTable,
    Row,
    Snapshot,
    TargetError,
    check_expectations,
    diff,
    digest,
    element_name,
    judge_step,
    live_ladder,
    loc_stable,
    normalize,
    read_screen,
    recordable_present,
    render_diff,
    render_row,
    render_rows,
    saved_ladder,
    settle,
    without_typed_value,
)

FIXTURE = [
    {"i": 0, "label": "Optics Store", "cls": "h1", "bounds": [8, 21, 1272, 58], "act": [],
     "loc": "xpath=//h1[normalize-space()='Optics Store']"},
    {"i": 1, "label": "Email", "cls": "label", "bounds": [8, 80, 46, 98], "act": ["tap"],
     "loc": "xpath=//label[normalize-space()='Email']"},
    {"i": 2, "label": "Email", "cls": "input", "bounds": [50, 79, 203, 100], "act": ["tap", "input"],
     "rid": "email", "loc": "css=#email"},
    {"i": 3, "label": "Password", "cls": "label", "bounds": [207, 80, 268, 98], "act": ["tap"],
     "loc": "xpath=//label[normalize-space()='Password']"},
    {"i": 4, "label": "Password", "cls": "input", "bounds": [272, 79, 425, 100],
     "act": ["tap", "input"], "rid": "password", "loc": 'css=[data-testid="password"]'},
    {"i": 5, "label": "Password for all users: secret", "cls": "p", "bounds": [8, 116, 1272, 134],
     "act": [], "loc": "xpath=//p[normalize-space()='Password for all users: secret']"},
    {"i": 6, "label": "Sign in", "cls": "button", "bounds": [8, 150, 64, 171], "act": ["tap"],
     "loc": "xpath=//button[normalize-space()='Sign in']"},
]


def _el(text, cls="android.widget.TextView", act=(), rid="", loc="", bounds=(0, 0, 10, 10)):
    extra = {"class": cls}
    if rid:
        extra["resource-id"] = rid
    entry = {
        "text": text,
        "bounds": dict(zip(("x1", "y1", "x2", "y2"), bounds)),
        "act": list(act),
        "extra": extra,
    }
    if loc:
        entry["loc"] = loc
    return entry


def _row(ref, label, cls="button", act=("tap",), rid="", loc=""):
    return Row(ref=ref, label=label, cls=cls, act=act, rid=rid, loc=loc)


def _snap(*rows):
    return Snapshot(rows=tuple(rows), digest=digest(rows))


class TestRender:
    def test_fixture_rows_golden(self):
        assert render_rows(RefTable().snapshot(FIXTURE)) == (
            "screen 21d99e · 7 rows\n"
            'e1 h1 "Optics Store"\n'
            'e2 label "Email" [tap]\n'
            'e3 input "Email" [tap,input] #email\n'
            'e4 label "Password" [tap]\n'
            'e5 input "Password" [tap,input] #password\n'
            'e6 p "Password for all users: secret"\n'
            'e7 button "Sign in" [tap]'
        )

    def test_wordless_native_icon(self):
        snap = RefTable().snapshot([
            _el("", cls="android.widget.ImageView", act=("tap",), rid="com.shop:id/search"),
        ])
        assert render_row(snap.rows[0]) == "e1 ImageView (no text) [tap] #search"

    def test_long_label_is_cut_at_80_chars(self):
        row = _row("e4", "a" * 85, cls="TextView", act=())
        assert render_row(row) == "e4 TextView \"" + "a" * 80 + "…\""

    def test_quote_and_newline_in_label(self):
        row = _row("e5", 'Say "hi"\n  to   all')
        assert render_row(row) == "e5 button \"Say 'hi' to all\" [tap]"

    def test_cut_keeps_actionable_rows_and_prints_footer(self):
        elements = [_el(f"Note {n}") for n in range(3)] + [_el("Buy", act=("tap",), loc="//b")]
        out = render_rows(RefTable().snapshot(elements, max_rows=2))
        assert out.splitlines()[1:] == [
            'e1 TextView "Note 0"',
            'e4 TextView "Buy" [tap]',
            '… 2 more rows hidden; observe(query="…") to find one',
        ]

    def test_query_keeps_matching_rows(self):
        snap = RefTable().snapshot([_el("Granite Mug"), _el("Basalt Lamp")], query="lamp")
        assert [render_row(r) for r in snap.rows] == ['e2 TextView "Basalt Lamp"']

    def test_unchanged_and_new_screen_forms(self):
        table = RefTable()
        first = table.snapshot([_el("Home", loc="//a")])
        same = table.snapshot([_el("Home", loc="//a")])
        assert render_diff(diff(first, same), same) == f"screen {same.digest} · unchanged"
        other = table.snapshot([_el("Settings", loc="//s")])
        assert render_diff(diff(same, other), other) == (
            f"screen {other.digest} · new screen\n" 'e2 TextView "Settings"'
        )


class TestNormalize:
    def test_internal_compact_shape(self):
        el = _el("Search", cls="XCUIElementTypeButton", act=("tap",), rid="search", loc="//x")
        assert normalize(el) == {
            "label": "Search", "cls": "Button", "act": ("tap",), "rid": "search",
            "loc": "//x", "bounds": (0, 0, 10, 10), "secret": False,
        }

    def test_verifier_projection(self):
        assert normalize(FIXTURE[2]) == {
            "label": "Email", "cls": "input", "act": ("tap", "input"), "rid": "email",
            "loc": "css=#email", "bounds": (50, 79, 203, 100), "secret": False,
        }

    def test_full_list_row_without_act_is_read_only(self):
        el = {"text": "Total", "bounds": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
              "xpath": "//p[@id='total']", "extra": {"class": "p"}}
        assert normalize(el) == {
            "label": "Total", "cls": "p", "act": (), "rid": "",
            "loc": "//p[@id='total']", "bounds": (1, 2, 3, 4), "secret": False,
        }

    def test_label_filled_with_id_or_class_counts_as_empty(self):
        by_id = _el("com.shop:id/cart", act=("tap",), rid="com.shop:id/cart")
        by_class = _el("android.widget.Button", cls="android.widget.Button", act=("tap",))
        assert normalize(by_id)["label"] == ""
        assert normalize(by_class)["label"] == ""

    def test_web_text_equal_to_its_id_or_tag_is_kept(self):
        login = {"text": "login", "act": ["tap"], "loc": "css=#login",
                 "extra": {"class": "button", "resource-id": "login"}}
        link = {"text": "a", "act": ["tap"], "loc": "xpath=//a[normalize-space()='a']", "extra": {"class": "a"}}
        assert normalize(login)["label"] == "login"
        assert normalize(link)["label"] == "a"

    def test_wordless_read_only_row_is_dropped(self):
        assert normalize(_el("")) is None
        assert normalize("garbage") is None


class TestRefIdentity:
    def test_stable_loc_keeps_ref_across_move_and_relabel(self):
        table = RefTable()
        before = table.snapshot([
            _el("Shop"), _el("Search", act=("tap",)),
            _el("Cart (0)", act=("tap",), loc="css=#cart"),
        ])
        after = table.snapshot([
            _el("Shop"), _el("Search", act=("tap",)),
            _el("Cart (1)", act=("tap",), loc="css=#cart", bounds=(0, 50, 10, 60)),
        ])
        assert after.row("e3").label == "Cart (1)"
        assert render_diff(diff(before, after), after) == (
            f"screen {after.digest} · changed (+0 -0 ~1)\n"
            '~ e3 TextView "Cart (1)" [tap] (was "Cart (0)")'
        )

    def test_locless_duplicate_gets_new_ref_when_it_moves(self):
        table = RefTable()
        first = table.snapshot([
            _el("Add", act=("tap",), bounds=(0, 0, 10, 10)),
            _el("Add", act=("tap",), bounds=(0, 20, 10, 30)),
        ])
        assert [r.ref for r in first.rows] == ["e1", "e2"]
        moved = table.snapshot([
            _el("Add", act=("tap",), bounds=(0, 20, 10, 30)),
            _el("Add", act=("tap",), bounds=(0, 40, 10, 50)),
        ])
        assert [r.ref for r in moved.rows] == ["e3", "e4"]

    def test_identical_rows_keep_their_refs_in_order(self):
        rows = [_el("Photo", cls="android.widget.ImageView", act=("tap",)) for _ in range(3)]
        table = RefTable()
        first = [r.ref for r in table.snapshot(rows).rows]
        assert [r.ref for r in table.snapshot(rows).rows] == first == ["e1", "e2", "e3"]

    def test_unstable_loc_is_keyed_by_position(self):
        table = RefTable()
        table.snapshot([_el("Row", loc="(//cell)[2]", bounds=(0, 0, 1, 1))])
        moved = table.snapshot([_el("Row", loc="(//cell)[2]", bounds=(0, 5, 1, 6))])
        assert moved.rows[0].ref == "e2"

    def test_positional_path_does_not_carry_a_ref_to_another_row(self):
        def contacts(*names):
            return [_el(n, cls="android.widget.LinearLayout", act=("tap",),
                        loc=f"//android.widget.FrameLayout/android.widget.LinearLayout[{i}]", bounds=(0, 0, 10, 10))
                    for i, n in enumerate(names, 1)]

        table = RefTable()
        alice = table.snapshot(contacts("Alice", "Bob")).rows[0].ref
        assert table.snapshot(contacts("Zoe", "Bob")).rows[0].ref != alice

    def test_shared_id_loc_does_not_carry_a_ref_to_another_screen(self):
        def page(name):
            return [
                _el(name, cls="android.widget.TextView", loc=f'//*[@text="{name}"]'),
                _el(f"Use {name}", cls="android.widget.TextView", loc=f'//*[@text="Use {name}"]'),
                _el("", cls="android.widget.Switch", act=("toggle",), rid="android:id/switch_widget",
                    loc="//android.widget.Switch", bounds=(900, 200, 1000, 260)),
            ]

        table = RefTable()
        wifi = table.snapshot(page("Wi-Fi"))
        old = wifi.rows[2].ref
        bluetooth = table.snapshot(page("Bluetooth"))
        assert bluetooth.rows[2].ref != old
        with pytest.raises(TargetError) as exc:
            table.resolve(old)
        assert exc.value.kind == "stale_ref"
        assert table.snapshot(page("Wi-Fi")).rows[2].ref == old

    def test_bare_tag_loc_is_keyed_by_position(self):
        table = RefTable()
        rows = [_el(f"Row {n}", loc=f'//*[@text="Row {n}"]') for n in range(4)]
        first = table.snapshot([*rows, _el("", cls="android.widget.Switch", act=("toggle",),
                                           loc="//android.widget.Switch", bounds=(0, 0, 5, 5))])
        scrolled = table.snapshot([*rows, _el("", cls="android.widget.Switch", act=("toggle",),
                                              loc="//android.widget.Switch", bounds=(0, 50, 5, 55))])
        assert scrolled.rows[4].ref != first.rows[4].ref

    @pytest.mark.parametrize("loc", ['//android.widget.EditText[@text="{}"]', ""])
    def test_typing_into_a_field_without_id_keeps_its_ref(self, loc):
        def form(value):
            field = _el(value, cls="android.widget.EditText", act=("tap", "input"), loc=loc.format(value))
            return [_el("Payment", loc="//*[@text='Payment']"), field,
                    _el("Phone", cls="android.widget.EditText", act=("input",), loc='//*[@text="Phone"]'),
                    _el("Pay", act=("tap",), loc="//*[@text='Pay']")]

        table = RefTable()
        before = table.snapshot(form("Card number"))
        after = table.snapshot(form("4242 4242 4242 4242"))
        assert [r.ref for r in after.rows] == [r.ref for r in before.rows]
        assert table.resolve(before.rows[1].ref).label == "4242 4242 4242 4242"
        assert diff(before, after).relabelled == ((after.rows[1], "Card number"),)

    def test_typing_into_a_field_with_id_keeps_its_ref(self):
        def form(value):
            return [_el(value, cls="android.widget.EditText", act=("tap", "input"), rid="com.app:id/email",
                        loc='//android.widget.EditText[@resource-id="com.app:id/email"]'),
                    _el("Next", act=("tap",), loc="//*[@text='Next']")]

        table = RefTable()
        before = table.snapshot(form("Email"))
        assert [r.ref for r in table.snapshot(form("a@b.c")).rows] == [r.ref for r in before.rows]

    def test_toggled_switch_keeps_its_ref(self):
        def row(state):
            return [_el("Dark mode", loc='//*[@text="Dark mode"]'),
                    _el(state, cls="android.widget.Switch", act=("toggle",),
                        loc=f'//android.widget.Switch[@text="{state}"]', bounds=(900, 0, 1000, 50)),
                    _el("Font size", act=("tap",), loc='//*[@text="Font size"]')]

        table = RefTable()
        before = table.snapshot(row("OFF"))
        assert [r.ref for r in table.snapshot(row("ON")).rows] == [r.ref for r in before.rows]

    @pytest.mark.parametrize(("cls", "rid"), [
        ("XCUIElementTypeTextField", ""), ("android.widget.EditText", "com.app:id/email"),
    ])
    def test_field_that_replaces_another_gets_a_new_ref(self, cls, rid):
        def step(header, name, field_cls, field_rid):
            loc = f'//*[@resource-id="{field_rid}"]' if field_rid else f'//{field_cls}[@text="{name}"]'
            return [_el(header, loc=f'//*[@text="{header}"]'),
                    _el(name, cls=field_cls, act=("tap", "input"), rid=field_rid, loc=loc, bounds=(0, 50, 10, 60)),
                    _el("Next", act=("tap",), loc="//*[@text='Next']")]

        table = RefTable()
        email = table.snapshot(step("Sign in", "a@b.c", cls, rid)).rows[1].ref
        other_cls = "XCUIElementTypeSecureTextField" if cls.startswith("XCUI") else cls
        password = table.snapshot(step("Sign in", "Password", other_cls, rid and "com.app:id/password"))
        assert password.rows[1].ref != email
        with pytest.raises(TargetError) as exc:
            table.resolve(email)
        assert exc.value.kind == "stale_ref"

    def test_field_on_a_new_step_gets_a_new_ref(self):
        def step(header, name):
            return [_el(header, loc=f'//*[@text="{header}"]'),
                    _el(name, cls="android.widget.EditText", act=("tap", "input"),
                        loc=f'//android.widget.EditText[@text="{name}"]', bounds=(0, 50, 10, 60))]

        table = RefTable()
        email = table.snapshot(step("Enter your email", "a@b.c")).rows[1].ref
        assert table.snapshot(step("Enter your password", "Password")).rows[1].ref != email

    def test_shared_rid_control_scrolled_into_another_list_row_gets_a_new_ref(self):
        def switch(y):
            return _el("", cls="android.widget.Switch", act=("toggle",), rid="android:id/switch_widget",
                       loc='//android.widget.Switch[@resource-id="android:id/switch_widget"]', bounds=(0, y, 10, y + 10))

        texts = [_el(f"T{n}") for n in range(1, 5)]
        table = RefTable()
        wifi = table.snapshot([_el("Wi-Fi"), switch(0), *texts]).rows[1].ref
        scrolled = table.snapshot([*texts, _el("Bluetooth"), switch(80)])
        assert scrolled.rows[5].ref != wifi
        toggled = table.snapshot([*texts, _el("Bluetooth"), switch(80)])
        assert toggled.rows[5].ref == scrolled.rows[5].ref

    def test_repeated_button_in_a_slot_now_holding_another_card_gets_a_new_ref(self):
        def carousel(*cards):
            rows = [_el("Featured", rid="com.shop:id/toolbar_title",
                        loc='//*[@resource-id="com.shop:id/toolbar_title"]')]
            for slot, (title, price) in enumerate(cards):
                x = slot * 500
                rows += [
                    _el(title, rid="com.shop:id/title", loc=f'//*[@resource-id="com.shop:id/title" and @text="{title}"]'),
                    _el(price, rid="com.shop:id/price", loc=f'//*[@resource-id="com.shop:id/price" and @text="{price}"]'),
                    _el("Add to cart", cls="android.widget.Button", act=("tap",), rid="com.shop:id/add",
                        loc=f'(//*[@resource-id="com.shop:id/add"])[{slot + 1}]', bounds=(x, 0, x + 400, 50)),
                ]
            return rows

        table = RefTable()
        before = table.snapshot(carousel(("Trail Runner", "$89"), ("City Sneaker", "$89")))
        after = table.snapshot(carousel(("City Sneaker", "$89"), ("Hiking Boot", "$120")))
        old_buttons = {r.ref for r in before.rows if r.label == "Add to cart"}
        assert not old_buttons & {r.ref for r in after.rows if r.label == "Add to cart"}
        assert before.row(after.rows[1].ref).label == "City Sneaker"

    def test_repeated_rows_before_a_carousel_button_do_not_carry_its_ref_to_the_next_card(self):
        def carousel(*titles):
            rows = []
            for slot, title in enumerate(titles):
                x = slot * 500
                rows.append(_el(title, rid="com.shop:id/title",
                                loc=f'//*[@resource-id="com.shop:id/title" and @text="{title}"]'))
                rows += [_el(text, rid=f"com.shop:id/{rid}", loc=f'(//*[@resource-id="com.shop:id/{rid}"])[{slot + 1}]')
                         for text, rid in (("In stock", "stock"), ("FREE delivery", "delivery"))]
                rows.append(_el("Add to cart", cls="android.widget.Button", act=("tap",), rid="com.shop:id/add",
                                loc=f'(//*[@resource-id="com.shop:id/add"])[{slot + 1}]', bounds=(x, 0, x + 400, 50)))
            return rows

        table = RefTable()
        before = table.snapshot(carousel("Trail Runner", "City Sneaker"))
        after = table.snapshot(carousel("City Sneaker", "Hiking Boot"))
        buttons = {r.ref for r in before.rows if r.label == "Add to cart"}
        assert not buttons & {r.ref for r in after.rows if r.label == "Add to cart"}

    def test_list_that_shrinks_under_a_repeated_subtitle_reports_the_moved_buttons_gone(self):
        def requests(*names):
            rows = []
            for i, name in enumerate(names):
                y = 300 + i * 220
                rows += [
                    _el(name, rid="com.social:id/name", loc=f'//*[@resource-id="com.social:id/name" and @text="{name}"]'),
                    _el("Sent you a friend request", rid="com.social:id/subtitle",
                        loc=f'(//*[@resource-id="com.social:id/subtitle"])[{i + 1}]'),
                ]
                rows += [_el(label, cls="android.widget.Button", act=("tap",), rid=f"com.social:id/{label.lower()}",
                             loc=f'(//*[@resource-id="com.social:id/{label.lower()}"])[{i + 1}]',
                             bounds=(x, y, x + 300, y + 80))
                         for label, x in (("Confirm", 200), ("Delete", 520))]
            return rows

        table = RefTable()
        before = table.snapshot(requests("Alice", "Bob", "Carol"))
        after = table.snapshot(requests("Bob", "Carol"))
        deletes = {r.ref for r in before.rows if r.label == "Delete"}
        assert not deletes & {r.ref for r in after.rows}
        assert deletes <= {r.ref for r in diff(before, after).removed}

    def test_filtered_checkbox_keeps_its_own_ref(self):
        def screen(query, brands):
            return [
                _el("Brand", loc='//android.widget.TextView[@text="Brand"]'),
                _el(query, cls="android.widget.EditText", act=("tap", "input"),
                    loc=f'//android.widget.EditText[@text="{query}"]'),
                *(_el(b, cls="android.widget.CheckBox", act=("tap", "toggle"), loc=f'//android.widget.CheckBox[@text="{b}"]',
                      bounds=(40, 400 + i * 120, 1040, 500 + i * 120)) for i, b in enumerate(brands)),
            ]

        table = RefTable()
        refs = {r.label: r.ref for r in table.snapshot(screen("Search brands", ["Nike", "Adidas", "Puma"])).rows}
        after = table.snapshot(screen("pu", ["Puma"]))
        assert after.rows[2].ref == refs["Puma"]
        with pytest.raises(TargetError):
            table.resolve(refs["Nike"])

    def test_unlabelled_icon_on_another_tab_gets_a_new_ref(self):
        def tab(*body):
            return [
                _el("Shop", loc='//*[@text="Shop"]'),
                _el("", cls="android.view.View", act=("tap",), loc="//android.widget.FrameLayout/android.view.View[1]",
                    bounds=(960, 100, 1040, 180)),
                *(_el(text, loc=f'//*[@text="{text}"]') for text in body),
            ]

        table = RefTable()
        cart = table.snapshot(tab("Deals of the day", "Trail Runner")).rows[1].ref
        assert table.snapshot(tab("Jane Doe", "Orders")).rows[1].ref != cart

    def test_overlay_that_closes_leaves_the_refs_under_it(self):
        form = [
            _el("Welcome back", cls="XCUIElementTypeStaticText", loc='//XCUIElementTypeStaticText[@name="Welcome back"]'),
            _el("email", cls="XCUIElementTypeTextField", act=("input",), loc='//XCUIElementTypeTextField[@name="email"]'),
            _el("Sign in", cls="XCUIElementTypeButton", act=("tap",), loc='//XCUIElementTypeButton[@name="Sign in"]'),
        ]
        keys = [_el(k, cls="XCUIElementTypeKey", act=("tap",), loc=f'//XCUIElementTypeKey[@name="{k}"]')
                for k in "qwertyuiop"]
        table = RefTable()
        before = table.snapshot(form)
        table.snapshot(form + keys)
        after = table.snapshot(form)
        assert [r.ref for r in after.rows] == [r.ref for r in before.rows]
        assert render_diff(diff(table.snapshot(form + keys), after), after).startswith(
            f"screen {after.digest} · changed (+0 -10 ~0)"
        )

    def test_listbox_that_closes_after_a_pick_leaves_the_form_refs(self):
        def form(country):
            return [
                {"label": "Checkout", "cls": "h1", "act": [], "loc": "xpath=//h1[normalize-space()='Checkout']"},
                {"label": "Full name", "cls": "input", "act": ["tap", "input"], "rid": "name", "loc": "css=#name"},
                {"label": country, "cls": "button", "act": ["tap"], "rid": "country", "loc": "css=#country"},
                {"label": "Place order", "cls": "button", "act": ["tap"], "loc": 'css=[data-testid="place-order"]'},
            ]

        options = [{"label": c, "cls": "li", "act": ["tap"], "loc": f'css=[role=option][data-value="{c}"]'}
                   for c in ("Austria", "India", "Japan")]
        table = RefTable()
        before = table.snapshot(form("Country"))
        table.snapshot(form("Country") + options)
        after = table.snapshot(form("Country: India"))
        assert [r.ref for r in after.rows] == [r.ref for r in before.rows]

    def test_filtering_a_list_leaves_the_search_field_and_toolbar_refs(self):
        def screen(query, names):
            return [
                _el("Contacts", rid="com.contacts:id/toolbar_title",
                    loc='//android.widget.TextView[@resource-id="com.contacts:id/toolbar_title"]'),
                _el(query, cls="android.widget.EditText", act=("input",), rid="com.contacts:id/search",
                    loc='//android.widget.EditText[@resource-id="com.contacts:id/search"]'),
                *[_el(n, cls="android.widget.LinearLayout", act=("tap",), bounds=(0, 100 * i, 10, 100 * i + 90))
                  for i, n in enumerate(names, 1)],
                _el("New contact", cls="android.widget.ImageButton", act=("tap",),
                    loc='//android.widget.ImageButton[@content-desc="New contact"]'),
            ]

        table = RefTable()
        before = table.snapshot(screen("Search contacts", ["Alice", "Bob", "Carla", "Zoe"]))
        after = table.snapshot(screen("zoe", ["Zoe"]))
        assert [r.ref for r in after.rows[:2]] == [r.ref for r in before.rows[:2]]
        assert after.rows[-1].ref == before.rows[-1].ref
        assert after.rows[2].ref not in {r.ref for r in before.rows}

    def test_fixed_unlabelled_button_keeps_its_ref_through_a_scroll(self):
        back = _el("", cls="android.widget.ImageButton", act=("tap",), rid="com.x:id/back",
                   loc='//android.widget.ImageButton[@resource-id="com.x:id/back"]', bounds=(0, 0, 50, 50))

        def items(first):
            return [_el(f"Item {n}", loc=f'//*[@text="Item {n}"]') for n in range(first, first + 6)]

        table = RefTable()
        before = table.snapshot([back, *items(1)])
        after = table.snapshot([back, *items(4)])
        assert after.rows[0].ref == before.rows[0].ref
        assert table.resolve(before.rows[0].ref).rid == "com.x:id/back"

    def test_ref_does_not_depend_on_bounds_scaling(self, monkeypatch):
        def double_when_framed(elements, source, frame):
            if frame is None:
                return
            for el in elements:
                el["bounds"] = {k: v * 2 for k, v in el["bounds"].items()}

        monkeypatch.setattr(
            "optics_framework.common.utils.scale_interactive_element_bounds", double_when_framed
        )
        ok, png = cv2.imencode(".png", np.zeros((4, 4, 3), dtype=np.uint8))
        add = '(//XCUIElementTypeButton[@name="Add"])[2]'

        def read(with_png):
            source = _ActionKeyword([_el("Add", cls="XCUIElementTypeButton", act=("tap",), loc=add)])
            source.element_source = type("Source", (), {"REQUIRED_DRIVER_TYPE": "appium"})()
            return read_screen(source, png.tobytes() if with_png else None)

        table = RefTable()
        scaled = table.snapshot(read(True))
        plain = table.snapshot(read(False))
        assert scaled.rows[0].bounds == (0, 0, 20, 20)
        assert plain.rows[0].ref == scaled.rows[0].ref

    def test_new_row_never_reuses_an_old_number(self):
        table = RefTable()
        table.snapshot([_el("One", loc="//one")])
        table.snapshot([_el("Two", loc="//two")])
        third = table.snapshot([_el("Three", loc="//three")])
        assert third.rows[0].ref == "e3"


class TestResolve:
    def _table(self):
        table = RefTable()
        table.snapshot([
            _el("Sign in", cls="button", act=("tap",), loc="//signin"),
            _el("Welcome"),
        ])
        table.snapshot([
            _el("Add", act=("tap",), loc="//add1"), _el("Add", act=("tap",), loc="//add2"),
            _el("Add"), _el("Cart", act=("tap",), loc="//cart"),
        ])
        return table

    def test_ref_on_screen(self):
        assert self._table().resolve("e6").label == "Cart"

    def test_gone_ref_is_stale(self):
        with pytest.raises(TargetError) as exc:
            self._table().resolve("e1")
        assert exc.value.kind == "stale_ref"
        assert str(exc.value) == 'e1 ("Sign in" button) is not on the current screen.'

    def test_never_issued_ref_is_unknown(self):
        with pytest.raises(TargetError) as exc:
            self._table().resolve("e99")
        assert exc.value.kind == "unknown_ref"
        assert str(exc.value) == "No e99; current refs are e3–e6."

    def test_unique_label_resolves_case_insensitively(self):
        assert self._table().resolve("cart").ref == "e6"

    def test_duplicate_label_is_ambiguous_among_actionable_rows(self):
        with pytest.raises(TargetError) as exc:
            self._table().resolve("Add")
        assert exc.value.kind == "ambiguous"
        assert str(exc.value) == '"Add" matches 2 rows: e3 "Add", e4 "Add". Pass one ref.'

    def test_locator_passes_through(self):
        assert self._table().resolve("css=#x") is None


class TestLadders:
    def test_live_ladder_holds_only_locators(self):
        snap = RefTable().snapshot(FIXTURE)
        assert live_ladder(snap.row("e5"), snap) == ['css=[data-testid="password"]', "css=#password"]
        assert live_ladder(snap.row("e7"), snap) == ["xpath=//button[normalize-space()='Sign in']"]

    def test_native_rid_rung(self):
        snap = RefTable().snapshot([
            _el("Go", act=("tap",), rid="com.shop:id/go", loc='//*[@text="Go"]'),
        ])
        assert live_ladder(snap.rows[0], snap) == [
            '//*[@text="Go"]', '//*[@resource-id="com.shop:id/go"]',
        ]

    def test_shared_rid_adds_no_rung(self):
        snap = RefTable().snapshot([
            _el("A", act=("tap",), rid="com.shop:id/item", loc="//a"),
            _el("B", act=("tap",), rid="com.shop:id/item", loc="//b"),
        ])
        assert live_ladder(snap.rows[0], snap) == ["//a"]

    def test_row_without_locator_raises(self):
        snap = RefTable().snapshot([_el("Note", act=("tap",))])
        with pytest.raises(TargetError) as exc:
            live_ladder(snap.rows[0], snap)
        assert exc.value.kind == "no_locator"

    def test_saved_ladder_puts_stable_rung_first_and_label_last(self):
        snap = RefTable().snapshot([
            _el("Next", cls="button", act=("tap",), rid="next", loc="xpath=/html/body/button"),
        ])
        assert saved_ladder(snap.rows[0], snap) == ["css=#next", "xpath=/html/body/button", "Next"]

    def test_native_indexed_loc_gets_no_rid_rung(self):
        loc = '(//android.widget.TextView[@text="Orders"])[1]'
        snap = RefTable().snapshot([_el("Orders", act=("tap",), rid="com.app:id/title", loc=loc)])
        assert live_ladder(snap.rows[0], snap) == [loc]
        assert saved_ladder(snap.rows[0], snap) == [loc, "Orders"]

    def test_saved_ladder_prefixes_locator_like_label(self):
        snap = RefTable().snapshot([_el("/home", act=("tap",), loc="//nav")])
        assert saved_ladder(snap.rows[0], snap) == ["//nav", "text=/home"]

    def test_loc_stable(self):
        assert loc_stable("//a[@id='x']")
        assert loc_stable("css=#x")
        assert not loc_stable("(//a)[2]")
        assert not loc_stable("/hierarchy/a")
        assert not loc_stable("xpath=/html/body")
        assert not loc_stable("")


class TestElementName:
    def test_label_and_class(self):
        assert element_name(_row("e1", "Sign in"), set()) == "sign_in_button"
        assert element_name(_row("e2", "Email", cls="input"), set()) == "email_input"

    def test_rid_tail_when_label_is_empty(self):
        row = _row("e3", "", cls="ImageView", rid="com.shop:id/search")
        assert element_name(row, set()) == "search_image_view"

    def test_clash_gets_a_number(self):
        taken = {"sign_in_button", "sign_in_button_2"}
        assert element_name(_row("e1", "Sign in"), taken) == "sign_in_button_3"


class TestExpectations:
    def _snap(self):
        return _snap(
            _row("e1", "Welcome, a@b.c", act=()),
            _row("e2", "Signed   in", act=()),
            _row("e3", "Orders"),
        )

    def test_present_matches_on_word_boundaries(self):
        checks = check_expectations(self._snap(), ["Welcome", "Sign", "signed in", "e3"], [])
        assert [c.met for c in checks] == [True, False, True, True]

    def test_absent(self):
        checks = check_expectations(self._snap(), [], ["Error", "orders", "e9"])
        assert checks == [
            Check("Error", "absent", True),
            Check("orders", "absent", False),
            Check("e9", "absent", True),
        ]

    def test_absent_matches_on_word_boundaries(self):
        checks = check_expectations(_snap(_row("e1", "Bookings")), ["OK"], ["OK"])
        assert [(c.expect, c.met) for c in checks] == [("present", False), ("absent", True)]

    def test_met_before_marks_checks_the_action_did_not_cause(self):
        before = _snap(_row("e1", "Password for all users"), _row("e2", "Error"))
        after = _snap(_row("e1", "Password for all users"), _row("e3", "Welcome"))
        checks = check_expectations(after, ["Password for all users", "Welcome"], ["Error"], before=before)
        assert [(c.met, c.met_before) for c in checks] == [(True, True), (True, False), (True, False)]

    def test_typed_value_check_is_dropped_only_while_unmet(self):
        checks = check_expectations(_snap(_row("e1", "Orders")), ["a@b.c", "Orders", "Welcome"], ["a@b.c"])
        assert without_typed_value(checks, " A@B.C ") == [
            Check("Orders", "present", True), Check("Welcome", "present", False), Check("a@b.c", "absent", True),
        ]
        assert without_typed_value(checks, "") == checks
        typed = check_expectations(self._snap(), ["a@b.c"], [])
        assert without_typed_value(typed, "a@b.c") == typed


class TestRecordablePresent:
    def test_refs_become_locators_and_partial_text_becomes_a_note(self):
        snap = RefTable().snapshot([
            _el("Welcome back, Ana"),
            _el("Orders", act=("tap",), rid="com.shop:id/orders", loc="//*[@resource-id='com.shop:id/orders']"),
            _el("Total", act=("tap",)),
            _el("Sign out"),
        ])
        items, notes = recordable_present(
            ["e2", "e3", "sign out", "Welcome", "css=#cart", "e99", " "], snap
        )
        assert items == ["//*[@resource-id='com.shop:id/orders']", "Total", "Sign out", "css=#cart"]
        assert notes == [
            '"Welcome" is not the whole text of a row, so it was not recorded',
            "e99 is not on the screen, so it was not recorded",
        ]


class TestJudgeStep:
    CHANGED = Diff(True, False, (), (), ())
    SAME = Diff(False, False, (), (), ())

    @pytest.mark.parametrize(
        ("ok", "changed", "checks", "verdict"),
        [
            (False, True, [Check("a", "present", True)], "fail"),
            (True, True, [], "unknown"),
            (True, False, [Check("a", "present", True)], "pass"),
            (True, False, [Check("a", "present", True, met_before=True)], "unknown"),
            (True, True, [Check("a", "present", True, met_before=True)], "pass"),
            (True, True, [Check("a", "absent", True)], "pass"),
            (True, False, [Check("a", "absent", True)], "unknown"),
            (True, False, [Check("a", "present", False)], "fail"),
            (True, True, [Check("a", "present", False)], "unknown"),
        ],
    )
    def test_table(self, ok, changed, checks, verdict):
        assert judge_step(ok, self.CHANGED if changed else self.SAME, checks) == verdict


class TestDigestAndDiff:
    def test_digest_ignores_bounds(self):
        a = RefTable().snapshot([_el("Home", bounds=(0, 0, 1, 1))])
        b = RefTable().snapshot([_el("Home", bounds=(5, 5, 9, 9))])
        assert a.digest == b.digest

    def test_new_screen_when_most_rows_are_gone_and_most_rows_are_new(self):
        table = RefTable()
        first = table.snapshot([_el(t, loc=f"//{t}") for t in ("a", "b", "c", "d")])
        half = table.snapshot([_el(t, loc=f"//{t}") for t in ("a", "b", "x")])
        assert diff(first, half).new_screen is False
        fewer = table.snapshot([_el(t, loc=f"//{t}") for t in ("a",)])
        assert diff(half, fewer).new_screen is False
        other = table.snapshot([_el(t, loc=f"//{t}") for t in ("p", "q")])
        assert diff(fewer, other).new_screen is True

    def test_added_and_removed_lines(self):
        table = RefTable()
        first = table.snapshot([_el("Sign in", act=("tap",), loc="//s"), _el("Hint", loc="//h")])
        second = table.snapshot([
            _el("Sign in", act=("tap",), loc="//s"), _el("Welcome, a@b.c", loc="//w"),
        ])
        assert render_diff(diff(first, second), second) == (
            f"screen {second.digest} · changed (+1 -1 ~0)\n"
            '+ e3 TextView "Welcome, a@b.c"\n'
            '- e2 "Hint"'
        )


class TestSettle:
    def test_stops_at_first_changed_read(self):
        reads = iter(["aaaaaa", "aaaaaa", "bbbbbb", "cccccc"])
        calls = []

        def read():
            calls.append(1)
            return Snapshot(rows=(), digest=next(reads))

        assert settle(read, "aaaaaa", pause_s=0, extra_reads=5).digest == "bbbbbb"
        assert len(calls) == 3

    def test_gives_up_after_extra_reads(self):
        def read():
            return Snapshot(rows=(), digest="aaaaaa")

        calls = []
        assert settle(lambda: calls.append(1) or read(), "aaaaaa", pause_s=0).digest == "aaaaaa"
        assert len(calls) == 3


class _Strategies:
    def __init__(self, result):
        self._result = result

    def get_interactive_elements(self, filter_config, compact=False):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _ActionKeyword:
    def __init__(self, result):
        self.strategy_manager = _Strategies(result)
        self.element_source = object()


class TestReadScreen:
    def test_returns_compact_elements(self):
        elements = [_el("Home")]
        assert read_screen(_ActionKeyword(elements), b"png") == [
            {**_el("Home"), "window_bounds": _el("Home")["bounds"]},
        ]

    def test_source_error_gives_none(self):
        error = OpticsError(Code.E0202, message="no elements")
        assert read_screen(_ActionKeyword(error)) is None
        assert read_screen(None) is None

    def test_dead_session_is_raised(self):
        with pytest.raises(OpticsError) as exc:
            read_screen(_ActionKeyword(OpticsError(Code.E0106, message="gone")))
        assert exc.value.code == Code.E0106
