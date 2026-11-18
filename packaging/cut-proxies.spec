Name:           silhouette-control-center
Version:        0.1.0
Release:        1%{?dist}
Summary:        Verified Silhouette Cameo card cut templates and cutting
Provides:       cut-proxies = %{version}-%{release}
# No upstream license is declared yet; do not invent redistribution terms.
License:        LicenseRef-Unknown
URL:            https://github.com/zoro11031/silhouette-linux
Source0:        https://github.com/zoro11031/silhouette-linux/releases/download/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3
Requires:       python3 >= 3.10
Requires:       python3-pyside6 >= 6.4
Requires:       python3-pillow
Requires:       poppler-utils
Recommends:     python3-pyusb
Recommends:     libusb1

%description
Qt 6, GTK4 and terminal front ends for Silhouette Cameo proxy-card templates,
cut-stream generation, and verification. Hardware cutting requires device
access permissions. Studio integration uses an existing WinBoat installation.

%prep
%setup -q

%build
# Pure Python, installed privately so this noarch package is not tied to
# the build machine's Python minor version.

%install
python3 packaging/install.py --destdir "%{buildroot}"

%check
PYTHONPYCACHEPREFIX="%{_builddir}/cut-proxies-pycache" python3 -c 'from cut_proxies.job import build; assert not build().blocked'

%files
%{_bindir}/cut-proxies
%{_bindir}/silhouette-control-center
%{_prefix}/lib/cut-proxies/
%{_datadir}/applications/org.printing.SilhouetteControlCenter.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.printing.SilhouetteControlCenter.svg
%{_datadir}/doc/silhouette-control-center/
