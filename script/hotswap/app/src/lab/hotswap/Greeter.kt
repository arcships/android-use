package lab.hotswap

/** Kotlin target: a lambda plus a call into an inline function from another file. */
object Greeter {
    fun greet(): String {
        val parts = listOf("kotlin", "A").map { it.uppercase() }
        return twice { parts.joinToString(" ") }
    }
}
