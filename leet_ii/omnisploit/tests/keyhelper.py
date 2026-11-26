"""Feed vim-style key strings to an Editor; <Name> tokens are special keys."""

SPECIAL = ("<Esc>", "<CR>", "<BS>", "<Up>", "<Down>", "<Left>", "<Right>", "<Tab>", "<Home>", "<End>", "<Del>")


def keys(ed, seq: str) -> None:
    i = 0
    while i < len(seq):
        if seq[i] == "<" and ">" in seq[i:] and seq[i : seq.index(">", i) + 1] in SPECIAL:
            j = seq.index(">", i) + 1
            ed.key(seq[i:j])
            i = j
        else:
            ed.key(seq[i])
            i += 1
