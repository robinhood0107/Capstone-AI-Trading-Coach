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
            val addresses = resolvePublic(uri.host)
            val response =
                try {
                    transport.get(uri, addresses.first(), MAX_BODY_BYTES)
                } catch (error: S49WebReadRejectedException) {
                    throw error
                } catch (_: Exception) {
                    reject("S4_9_WEB_READ_TRANSPORT_REJECTED")
                }
            val body = response.body
            try {
                ensure(body.size in 1..MAX_BODY_BYTES, "S4_9_WEB_READ_BODY_SIZE_REJECTED")
                ensure(resolvePublic(uri.host).toSet() == addresses.toSet(), "S4_9_WEB_READ_DNS_DRIFT_REJECTED")
                if (response.statusCode in 300..399) {
                    ensure(redirectCount < MAX_REDIRECTS, "S4_9_WEB_READ_REDIRECT_REJECTED")
                    val location =
                        response.headers["location"]?.singleOrNull()
                            ?: reject("S4_9_WEB_READ_REDIRECT_REJECTED")
                    uri = validateUri(uri.resolve(location).toASCIIString())
                    return@repeat
                }
                ensure(response.statusCode in 200..299, "S4_9_WEB_READ_HTTP_STATUS_REJECTED")
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
        reject("S4_9_WEB_READ_REDIRECT_REJECTED")
    }

    internal fun validateUri(value: String): URI {
        try {
            ensure(value.length in 1..2_048, "S4_9_WEB_READ_URL_REJECTED")
            val uri = URI.create(value).normalize()
            ensure(uri.scheme == "https" && uri.isAbsolute && uri.host != null, "S4_9_WEB_READ_URL_REJECTED")
            ensure(uri.rawUserInfo == null && uri.rawFragment == null, "S4_9_WEB_READ_URL_REJECTED")
            ensure(uri.port in setOf(-1, 443), "S4_9_WEB_READ_URL_REJECTED")
            return uri
        } catch (error: S49WebReadRejectedException) {
            throw error
        } catch (_: Exception) {
            reject("S4_9_WEB_READ_URL_REJECTED")
        }
    }

    private fun resolvePublic(host: String): List<InetAddress> =
        try {
            resolver.resolvePublic(host)
        } catch (error: S49WebReadRejectedException) {
            throw error
        } catch (_: Exception) {
            reject("S4_9_WEB_READ_DNS_REJECTED")
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
                    // 로그인 메뉴가 있는 정상 문서를 로그인 페이지로 오인하지 않고 실제 인증 폼만 차단한다.
                    ensure(
                        document.select("input[type=password]").isEmpty() &&
                            !LOGIN_PAGE_TITLE.containsMatchIn(document.title()),
                        "S4_9_WEB_READ_LOGIN_PAGE_REJECTED",
                    )
                    document.select("script,style,noscript,iframe,form,nav,header,footer,aside,[role=navigation]").remove()
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
                else -> reject("S4_9_WEB_READ_MIME_REJECTED")
            }
        val text =
            raw.second
                .replace(WHITESPACE, " ")
                .trim()
                .take(MAX_TEXT_CHARS)
        ensure(text.isNotBlank(), "S4_9_WEB_READ_TEXT_EMPTY")
        ensure(!PROMPT_INJECTION.containsMatchIn(text), "S4_9_WEB_READ_PROMPT_INJECTION_REJECTED")
        val title = sanitizePublicWebSearchText(raw.first.ifBlank { uri.host }, MAX_TITLE_CHARS).ifBlank { uri.host }
        return title to text
    }

    private fun ensure(
        condition: Boolean,
        leaf: String,
    ) {
        if (!condition) reject(leaf)
    }

    private fun reject(leaf: String): Nothing = throw S49WebReadRejectedException(leaf)

    private companion object {
        val WHITESPACE = Regex("\\s+")
        val LOGIN_PAGE_TITLE = Regex("(sign\\s*in|log\\s*in|login|password|로그인|비밀번호)", RegexOption.IGNORE_CASE)
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

/** MCP 오류에는 URL이나 응답 본문 대신 고정된 content-free leaf만 노출한다. */
class S49WebReadRejectedException(
    leaf: String,
) : IllegalArgumentException(leaf)

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
                val ipv4Translation =
                    matchesPrefix(bytes, NAT64_WELL_KNOWN) ||
                        matchesPrefix(bytes, NAT64_LOCAL_USE) ||
                        matchesPrefix(bytes, IPV4_MAPPED)
                val tunnel = matchesPrefix(bytes, TEREDO) || matchesPrefix(bytes, SIX_TO_FOUR)
                val discardOnly = matchesPrefix(bytes, DISCARD_ONLY)
                !uniqueLocal && !documentation && !ipv4Translation && !tunnel && !discardOnly
            }
            else -> false
        }
    }

    private fun matchesPrefix(
        address: ByteArray,
        prefix: IntArray,
    ): Boolean = prefix.indices.all { index -> address[index].toInt() and 0xff == prefix[index] }

    private companion object {
        val NAT64_WELL_KNOWN = intArrayOf(0x00, 0x64, 0xff, 0x9b, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)
        val NAT64_LOCAL_USE = intArrayOf(0x00, 0x64, 0xff, 0x9b, 0x00, 0x01)
        val IPV4_MAPPED = intArrayOf(0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0xff)
        val TEREDO = intArrayOf(0x20, 0x01, 0x00, 0x00)
        val SIX_TO_FOUR = intArrayOf(0x20, 0x02)
        val DISCARD_ONLY = intArrayOf(0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)
    }
}
