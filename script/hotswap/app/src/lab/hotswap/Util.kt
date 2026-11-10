package lab.hotswap

/** Inline: its body is copied into every caller, so editing it must recompile them. */
inline fun twice(block: () -> String): String = block() + " x1"
