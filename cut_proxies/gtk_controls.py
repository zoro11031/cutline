"""Small native controls shared by the GTK workspaces."""

from gi.repository import Gtk


def disclosure(parent, title="Advanced Options"):
    toggle = Gtk.Expander(label=title, hexpand=True)
    toggle.get_label_widget().add_css_class("heading")
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=10)
    toggle.set_child(body)
    parent.append(toggle)
    return body, toggle
