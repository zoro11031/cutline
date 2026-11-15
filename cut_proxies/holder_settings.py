"""Keep the two tool holders' edits independent in a desktop session."""

from dataclasses import replace


def for_holder(settings, holder):
    return replace(settings, holder=holder, depth=settings.depth if holder == 1 else None)


class HolderSettings:
    def __init__(self):
        self.active = 1
        self.drafts = {}

    def switch(self, holder, preset, settings):
        if holder == self.active:
            return None
        self.drafts[self.active] = preset, for_holder(settings, self.active)
        self.active = holder
        return self.drafts.get(holder, (preset, for_holder(settings, holder)))

    def profiles(self, preset, settings):
        profiles = {slot: self.drafts.get(slot, (preset, for_holder(settings, slot))) for slot in (1, 2)}
        profiles[self.active] = preset, for_holder(settings, self.active)
        return profiles
