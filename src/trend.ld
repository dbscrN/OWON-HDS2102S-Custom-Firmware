/* Add-on code replaces part of the 18 px CJK glyph table (0x0804A496..0x0804E402). */
SECTIONS
{
    . = 0x0804A510;
    .text : { *(.text*) *(.rodata*) }
    _end = .;
    ASSERT(_end <= 0x0804E400, "add-on code runs past the CJK glyph table")
    .data : { *(.data*) }
    .bss : { *(.bss*) *(COMMON) }
    ASSERT(SIZEOF(.data) == 0, "no .data allowed")
    ASSERT(SIZEOF(.bss) == 0, "no .bss allowed")
    /DISCARD/ : { *(.ARM.*) *(.comment) *(.note*) }
}
