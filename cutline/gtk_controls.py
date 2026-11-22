"""Small native controls shared by the GTK workspaces."""

from gi.repository import Adw, Gdk, Gio, GLib, Gtk


def workspace_header(design, send, *, active):
    header = Adw.HeaderBar()
    header.set_title_widget(Adw.WindowTitle(title="Cutline"))
    switch = Gtk.Box(spacing=4)
    buttons = []
    for name, callback in (("Design", design), ("Send", send)):
        # A toggle, so assistive technology hears which workspace is current.
        # Each workspace has its own header, so its state never changes:
        # a click switches workspace and the press is put back afterwards.
        current = name == active
        button = Gtk.ToggleButton(label=name, active=current)
        button.connect("clicked", callback)
        button.connect("clicked", lambda widget, keep=current: widget.set_active(keep))
        if current:
            button.add_css_class("suggested-action")
            button.update_property([Gtk.AccessibleProperty.DESCRIPTION], ["Current workspace"])
        switch.append(button)
        buttons.append(button)
    header.pack_end(switch)
    return header, *buttons


def workspace_tools(**properties):
    return Gtk.Box(spacing=8, **properties)


def zoom_controls(zoom_by, fit, *, fit_button=None):
    box = Gtk.Box(spacing=4)
    level = Gtk.Label(label="100%", width_chars=5)
    for label, tip, action in (("−", "Zoom out", lambda: zoom_by(0.8)),
                               ("+", "Zoom in", lambda: zoom_by(1.25))):
        widget = Gtk.Button(label=label, tooltip_text=tip)
        widget.connect("clicked", lambda _button, call=action: call())
        box.append(widget)
    box.insert_child_after(level, box.get_first_child())
    if fit_button is None:
        fit_button = Gtk.Button(label="Fit", tooltip_text="Fit to view")
        fit_button.connect("clicked", lambda _button: fit())
    box.append(fit_button)
    return box, level


def protect_settings_wheel(widget):
    if isinstance(widget, (Gtk.SpinButton, Gtk.Scale, Gtk.DropDown, Adw.SpinRow, Adw.ComboRow)):
        controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.set_propagation_limit(Gtk.PropagationLimit.SAME_NATIVE)
        controller.connect("scroll", _scroll_settings_pane)
        widget.add_controller(controller)
        return
    child = widget.get_first_child()
    while child is not None:
        protect_settings_wheel(child)
        child = child.get_next_sibling()


def _scroll_settings_pane(controller, _dx, delta):
    pane = controller.get_widget().get_ancestor(Gtk.ScrolledWindow)
    if pane is not None:
        adjustment = pane.get_vadjustment()
        step = adjustment.get_step_increment() if controller.get_unit() == Gdk.ScrollUnit.WHEEL else 1
        adjustment.set_value(adjustment.get_value() + delta * step)
    return True


def disclosure(parent, title="Advanced Options"):
    toggle = Gtk.Expander(label=title, hexpand=True)
    toggle.get_label_widget().add_css_class("heading")
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=10)
    toggle.set_child(body)
    parent.append(toggle)
    return body, toggle


def menu_item(label, action, shortcut=None):
    """A menu entry that shows its keyboard shortcut, as Qt's menus do."""
    item = Gio.MenuItem.new(label, action)
    if shortcut:
        item.set_attribute_value("accel", GLib.Variant.new_string(shortcut.split("|")[0]))
    return item
