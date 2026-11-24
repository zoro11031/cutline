# Cutline

I built Cutline for myself so I could cut Magic: The Gathering (MTG) proxies
with my Silhouette Cameo 4 on Linux. I added design and cutting tools so my wife
could use it for her own hobbies and projects, too. It's a hobby project, and I'm
sharing it here so the community can use it.

![A proxy sheet ready to cut](screenshots/send-workspace.png)

## What you can make

- Lay out eight proxies on a Letter or A4 sheet and cut them after printing.
- Make stickers, gift tags, or other designs with your own artwork, shapes, and
  text.
- Open a design you already have, including SVG, DXF, and Silhouette Studio
  files.

![Gift tags in the Design workspace](screenshots/design-workspace.png)

## Getting started

Download Cutline from [GitHub Releases](https://github.com/zoro11031/cutline/releases).
On Linux, you can use the AppImage or Flatpak with the included install script.
Fedora and Arch packages are also available, along with an experimental Windows
installer.

For the AppImage or Flatpak, put `install-cutline.sh` next to the download and run:

```sh
sh install-cutline.sh --appimage    # or --flatpak
sh install-cutline.sh --udev        # once for USB access on Linux
```

Open Cutline, create or open a design, then switch to **Send** and pick your
material. For Print & Cut, print at **Actual size (100%)** and choose
**Test Registration** before cutting. Use **Dial In a Material** to try a
small cut on a scrap.

## Cutter support

I've used Cutline to cut printed sheets on my Cameo 4 over USB. Those cuts
landed slightly off-center. Bluetooth cutting and the other Cameo and
Portrait models still need hardware testing. See the [testing status](docs/status.md)
and [project documentation](docs/README.md) for details.

## Thanks

Thanks to Alan Cha for the card layouts and cutting guides behind Cutline's
proxy workflow, and to everyone in the Discord community who shares tips and
helps others get their cuts right.
[Silhouette Card Maker](https://alan-cha.github.io/silhouette-card-maker/) ·
[Silhouette Card Makers Discord](https://discord.gg/jhsKmAgbXc)

Cutline is an independent project, unaffiliated with Silhouette America.
It's licensed under the [GNU GPL v3](LICENSE).
