package com.capstone.decision.infrastructure.mcp

import org.apache.pdfbox.Loader
import org.apache.pdfbox.text.PDFTextStripper
import org.jsoup.Jsoup
import org.springframework.stereotype.Component
import java.io.ByteArrayInputStream
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI

data class BoundedWebDocument(
    val canonicalUrl: String,
    val title: String,
    val text: String,
    val contentType: String,
)

fun interface PublicWebReaderPort {
    fun read(rawUrl: String): BoundedWebDocument
}

/** URL reader는 redirect마다 DNS를 재검증하고 cookie/credential 없이 bounded public HTTPS 문서만 읽는다. */
@Component
class SafePublicWebReader(
    private val resolver: PublicHostResolver = JdkPublicHostResolver(),
    private val transport: PublicHttpsTransport = PinnedPublicHttpsTransport(),
) : PublicWebReaderPort {
    override fun read(rawUrl: String): BoundedWebDocument {
        var uri = validateUri(rawUrl)
        repeat(MAX_REDIRECTS + 1) { redirectCount ->
            val addresses = resolver.resolvePublic(uri.host)
            val response = transport.get(uri, addresses.first(), MAX_BODY_BYTES)
            val body = response.body
            try {
                require(body.size in 1..MAX_BODY_BYTES)
                require(resolver.resolvePublic(uri.host).toSet() == addresses.toSet())
                if (response.statusCode in 300..399) {
                    require(redirectCount < MAX_REDIRECTS)
                    val location =
                        response.headers["location"]?.singleOrNull() ?: throw IllegalArgumentException("Redirect location missing")
                    uri = validateUri(uri.resolve(location).toASCIIString())
                    return@repeat
                }
                require(response.statusCode in 200..299)
                val contentType =
                    response.headers["content-type"]
                        ?.singleOrNull()
                        .orEmpty()
                        .substringBefore(';')
                        .trim()
                        .lowercase()
                val (title, text) = normalize(contentType, body, uri)
                return BoundedWebDocument(uri.toASCIIString(), title, text, contentType)
            } finally {
                body.fill(0)
            }
        }
        throw IllegalArgumentException("Redirect budget exceeded")
    }

    internal fun validateUri(value: String): URI {
        require(value.length in 1..2_048)
        val uri = URI.create(value).normalize()
        require(uri.scheme == "https" && uri.isAbsolute && uri.host != null)
        require(uri.rawUserInfo == null && uri.rawFragment == null)
        require(uri.port in setOf(-1, 443))
        return uri
    }

    private fun normalize(
        contentType: String,
        body: ByteArray,
        uri: URI,
    ): Pair<String, String> {
        val raw =
            when (contentType) {
                "text/html", "application/xhtml+xml" -> {
                    val document = Jsoup.parse(ByteArrayInputStream(body), null, uri.toASCIIString())
                    document.select("script,style,noscript,iframe,form").remove()
                    document.title().take(MAX_TITLE_CHARS) to document.body().text()
                }
                "text/plain" -> uri.host to body.toString(Charsets.UTF_8)
                "application/pdf" -> {
                    Loader.loadPDF(body).use { document ->
                        require(document.numberOfPages in 1..MAX_PDF_PAGES)
                        val stripper = PDFTextStripper().apply { endPage = MAX_PDF_PAGES }
                        (document.documentInformation.title ?: uri.host).take(MAX_TITLE_CHARS) to stripper.getText(document)
                    }
                }
                else -> throw IllegalArgumentException("Unsupported MIME type")
            }
        val text =
            raw.second
                .replace(WHITESPACE, " ")
                .trim()
                .take(MAX_TEXT_CHARS)
        require(text.isNotBlank())
        require(!LOGIN_PAGE.containsMatchIn(raw.first + " " + text.take(2_048)))
        require(!PROMPT_INJECTION.containsMatchIn(text.take(8_192)))
        return raw.first.ifBlank { uri.host }.take(MAX_TITLE_CHARS) to text
    }

    private companion object {
        val WHITESPACE = Regex("\\s+")
        val LOGIN_PAGE = Regex("(sign\\s*in|log\\s*in|password|로그인|비밀번호)", RegexOption.IGNORE_CASE)
        val PROMPT_INJECTION =
            Regex(
                "(ignore|disregard|override|forget).{0,48}(previous|system|developer|instructions?|prompt)|" +
                    "(system|developer)\\s*(message|prompt)|프롬프트.{0,32}(무시|덮어|지시)|이전.{0,24}지시.{0,24}무시",
                RegexOption.IGNORE_CASE,
            )
        const val MAX_REDIRECTS = 3
        const val MAX_BODY_BYTES = 2_000_000
        const val MAX_TEXT_CHARS = 60_000
        const val MAX_TITLE_CHARS = 256
        const val MAX_PDF_PAGES = 20
    }
}

fun interface PublicHostResolver {
    fun resolvePublic(host: String): List<InetAddress>
}

class JdkPublicHostResolver : PublicHostResolver {
    override fun resolvePublic(host: String): List<InetAddress> {
        require(host.isNotBlank() && host.length <= 253)
        val addresses = InetAddress.getAllByName(host).toList()
        require(addresses.isNotEmpty() && addresses.all(::isPublic))
        return addresses
    }

    private fun isPublic(address: InetAddress): Boolean {
        if (address.isAnyLocalAddress ||
            address.isLoopbackAddress ||
            address.isLinkLocalAddress ||
            address.isSiteLocalAddress ||
            address.isMulticastAddress
        ) {
            return false
        }
        val bytes = address.address
        return when (address) {
            is Inet4Address -> {
                val first = bytes[0].toInt() and 0xff
                val second = bytes[1].toInt() and 0xff
                val third = bytes[2].toInt() and 0xff
                !(
                    first == 0 ||
                        first == 10 ||
                        first == 127 ||
                        first >= 224 ||
                        (first == 100 && second in 64..127) ||
                        (first == 169 && second == 254) ||
                        (first == 172 && second in 16..31) ||
                        (first == 192 && second == 168) ||
                        (first == 192 && second == 0) ||
                        (first == 192 && second == 2) ||
                        (first == 198 && second in 18..19) ||
                        (first == 198 && second == 51 && third == 100) ||
                        (first == 203 && second == 0 && third == 113)
                )
            }
            is Inet6Address -> {
                val uniqueLocal = (bytes[0].toInt() and 0xfe) == 0xfc
                val documentation =
                    bytes[0].toInt() and 0xff == 0x20 &&
                        bytes[1].toInt() and 0xff == 0x01 &&
                        bytes[2].toInt() and 0xff == 0x0d &&
                        bytes[3].toInt() and 0xff == 0xb8
                !uniqueLocal && !documentation
            }
            else -> false
        }
    }
}
