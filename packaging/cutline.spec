Name:           cutline
Version:        0.3.3
Release:        1%{?dist}
Summary:        Design, print and cut with Silhouette cutters
Obsoletes:      silhouette-control-center < %{version}
# No upstream license is declared yet; do not invent redistribution terms.
License:        LicenseRef-Unknown
URL:            https://github.com/zoro11031/cutline
Source0:        https://github.com/zoro11031/cutline/releases/download/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3
Requires:       python3 >= 3.10
Requires:       python3-pyside6 >= 6.4
Requires:       python3-pillow
Requires:       poppler-utils
Requires:       geos >= 3.10
Requires:       pango
Recommends:     python3-pyusb
Recommends:     libusb1

%description
Design cut files, print them with registration marks and send verified jobs
to Silhouette cutters, with Qt 6, GTK4 and terminal front ends. Hardware cutting requires device
access permissions.

%prep
%setup -q

%build
# Pure Python, installed privately so this noarch package is not tied to
# the build machine's Python minor version.

%install
python3 packaging/install.py --destdir "%{buildroot}"

%check
PYTHONPYCACHEPREFIX="%{_builddir}/cutline-pycache" python3 -c 'from cutline.job import build; assert not build().blocked'

%files
%{_bindir}/cutline
%{_prefix}/lib/cutline/
%{_datadir}/applications/io.github.zoro11031.Cutline.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.zoro11031.Cutline.svg
%{_datadir}/doc/cutline/
%{_datadir}/licenses/cutline/
