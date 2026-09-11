# W800i bring-up references

- User firmware source: https://firmware.center/firmware/Sony-Ericsson/W800/
- MAIN: `CID 49 RED/W800_R1L002_MAIN_EU_EMEA_RED49.bin`
- FS: `CID 49 RED/W800_R1L002_FS_EMEA1_RED49.bin`
- BABE structure reference (read, not compiled or executed):
  https://github.com/farid1991/seftool/blob/main/src/emp/v3/babe.h
  and https://github.com/farid1991/seftool/blob/main/src/emp/v3/flash.c
- QEMU source: https://download.qemu.org/qemu-10.2.2.tar.xz
  Observed archive SHA-256, pinned by the build script:
  `784b296ff29c1417aa72323abcb2d2ea9ab9771724f577dcd785c3b04f21e176`.
  GPL-2.0; see the bundled QEMU COPYING file. The local W800 machine uses
  GPL-2.0-or-later and runs with QEMU's TCG ARM926 backend.
- ARM926EJ-S Technical Reference Manual, DDI0198E, TCM registers and size:
  https://documentation-service.arm.com/static/5e8e3d1088295d1e18d3a9b2
- Firmware runtime trace and static disassembly are the basis for the DB2010
  memory-range and PLL hypotheses. No DB2010 hardware datasheet was supplied.
- Intel StrataFlash Wireless Memory (L18), datasheet 251902 revision 010,
  August 2005, downloaded as `intel-l18.pdf`:
  https://tvsat.com.pl/PDF/G/GE28F128L18_int.pdf
  Read-while-write partition behavior is described in section 14 (page 71);
  the command descriptions cover the 32-word write buffer. This is an Intel
  document hosted by a distributor. It supports the implemented command
  behavior, but does not independently identify the flash fitted to this phone.

Only file downloads and source reads were performed against external sites.
No connected handset, serial port or physical flash device was accessed.

- DB2010 chipset revision identifiers in the original seftool source definitions:
  https://github.com/farid1991/seftool/blob/main/src/core/common.h
  `DB2010_1=0x8000`, `DB2010_2=0x8040`. Read as protocol documentation only.
  The separate ROM API experiment uses these identifiers; it is not part of
  the validated default bring-up build.
# SEFStool4 static compatibility reference

The preserved executable `SEFStool4.exe` comes from
[the SE-NSE archive](https://archive.org/details/se-nse-collection-of-sonyericsson-software),
`SE-NSE.rar`, member `SE-NSE/Usefull Content/Software/Extract FS/SEFStool4.exe`.
Size 69,632 bytes; SHA-256
`dc6fa3fb7328a951b608d7ae0e886fdb0b4b6cfb51fefb59e9a6e0338e5d02b7`.
It was inspected as data and never executed. Its v4.7 parser requires raw FS
version 0x1a, whereas supplied W800 FS is 0x17. See
`../reports/fs-extractor-source-search.md`. No license or source availability
is inferred from its preservation; this executable is not incorporated into
the emulator.
