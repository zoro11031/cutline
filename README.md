# Cutline

I built Cutline to cut my Magic: The Gathering proxies on Linux with a
Silhouette Cameo 4. I added the design tools so my wife could use it for her
own projects. It's a hobby project, and I'm sharing it so others can use it too.

![A proxy sheet ready to cut](screenshots/send-workspace.png)

## What you can make

- Lay out eight proxies on a Letter or A4 sheet and cut them after printing.
- Make stickers, gift tags, or other designs with your own artwork, shapes, and
  text.
- Open a design you already have, including SVG, DXF, and Silhouette Studio
  files.

![Gift tags in the Design workspace](screenshots/design-workspace.png)

## Getting started

Download the AppImage, Flatpak, or experimental Windows installer.
[GitHub Releases](https://github.com/zoro11031/cutline/releases)

If Cutline can't access your cutter over USB on Linux, run this once and
reconnect the cutter:

```sh
sh install-cutline.sh --udev
```

Open Cutline, create or open a design, then switch to **Send** and pick your
material. For Print & Cut, print at **Actual size (100%)** and choose
**Test Registration** before cutting. Use **Dial In a Material** to try a
small cut on a scrap.

## Cutter support

I've only tested Cutline with my Cameo 4 over USB and Bluetooth.
Your mileage may vary with other cutters.

## Thanks

Thanks to Alan Cha for the card layouts and cutting guides, and to the
Discord community for sharing what works.
[Silhouette Card Maker](https://alan-cha.github.io/silhouette-card-maker/) ·
[Silhouette Card Makers Discord](https://discord.gg/jhsKmAgbXc)

Cutline is an independent project, unaffiliated with Silhouette America.
It's licensed under the [GNU GPL v3](LICENSE).
