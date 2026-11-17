"""Small native controls shared by the GTK workspaces."""

from gi.repository import Adw, Gtk


def workspace_header(design, send, *, active):
    header = Adw.HeaderBar()
    header.set_title_widget(Adw.WindowTitle(title="Silhouette Control Center"))
    switch = Gtk.Box(spacing=4)
    buttons = []
    for name, callback in (("Design", design), ("Send", send)):
        button = Gtk.Button(label=name)
        button.connect("clicked", callback)
        if name == active:
            button.add_css_class("suggested-action")
            button.update_property([Gtk.AccessibleProperty.DESCRIPTION], ["Current workspace"])
        switch.append(button)
        buttons.append(button)
    header.pack_end(switch)
    return header, *buttons


def workspace_tools(**properties):
    return Gtk.Box(spacing=8, **properties)


def zoom_controls(zoom_by, fit):
    box = Gtk.Box(spacing=4)
    level = Gtk.Label(label="100%", width_chars=5)
    for label, tip, action in (("−", "Zoom out", lambda: zoom_by(0.8)),
                               ("+", "Zoom in", lambda: zoom_by(1.25)),
                               ("Fit", "Fit to view", fit)):
        widget = Gtk.Button(label=label, tooltip_text=tip)
        widget.connect("clicked", lambda _button, call=action: call())
        box.append(widget)
    box.insert_child_after(level, box.get_first_child())
    return box, level


def disclosure(parent, title="Advanced Options"):
    toggle = Gtk.Expander(label=title, hexpand=True)
    toggle.get_label_widget().add_css_class("heading")
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=10)
    toggle.set_child(body)
    parent.append(toggle)
    return body, toggle
