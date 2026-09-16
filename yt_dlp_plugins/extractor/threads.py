"""Threads (threads.net / threads.com) extractor plugin for yt-dlp.

Threads is not supported by mainline yt-dlp. Post pages are now empty JS
shells with no server-rendered post data, so this extractor talks to the
same GraphQL endpoint the web app uses:

1. GET the post page - yields a csrftoken cookie and an LSD token
2. Derive the numeric post ID from the URL shortcode (base64url, the same
   scheme Instagram media IDs use)
3. POST /api/graphql (BarcelonaPostPageContentQuery) - returns the post
   JSON including video_versions CDN URLs

With a logged-in session (browser cookies) the page is not empty: it embeds
the post's Relay data, which is used directly instead of the API (the
authenticated API form is rejected as of 2026-08). Quote posts and reposts
resolve to the post they wrap; Threads only exposes that to logged-in
sessions.

Works anonymously for public posts (verified 2026-08); login-walled posts
and quote posts need browser cookies (Settings > Instagram/Threads login).

yt-dlp auto-discovers this file because it lives in a ``yt_dlp_plugins``
package on sys.path (in development that's the script directory; in the
packaged exe it's bundled via build.py).
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from yt_dlp.cookies import YoutubeDLCookieJar
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking import Request
from yt_dlp.utils import (
    ExtractorError,
    strftime_or_none,
    urlencode_postdata,
)
from yt_dlp.utils.traversal import traverse_obj

# Instagram/Threads shortcode alphabet: a shortcode is the media's numeric
# ID in base64url
_SHORTCODE_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'

# Friendly names for the sites cross-posts commonly point at, keyed by the
# second-to-last domain label (instagram.com -> instagram, youtu.be -> youtu)
_LINK_SOURCE_NAMES = {
    'instagram': 'Instagram',
    'facebook': 'Facebook',
    'fb': 'Facebook',
    'youtube': 'YouTube',
    'youtu': 'YouTube',
    'tiktok': 'TikTok',
    'twitter': 'X (Twitter)',
    'x': 'X (Twitter)',
}


def _link_source_name(url):
    """Human-readable site name for a shared link, e.g. 'Instagram'."""
    m = re.match(r'https?://(?:www\.)?([^/:?#]+)', url or '')
    if not m:
        return 'the original site'
    domain = m.group(1).lower()
    labels = domain.split('.')
    key = labels[-2] if len(labels) >= 2 else labels[0]
    return _LINK_SOURCE_NAMES.get(key, domain)


class ThreadsIE(InfoExtractor):
    IE_NAME = 'threads'
    _VALID_URL = r'https?://(?:www\.)?threads\.(?:net|com)/(?P<uploader>[^/?#]+)/post/(?P<id>[^/?#&]+)'

    # Relay doc_id of BarcelonaPostPageContentQuery; Meta rotates these but
    # old ones stay valid for a long time
    _GRAPHQL_DOC_ID = '25460088156920903'

    # Meta serves an empty response to yt-dlp's default User-Agent
    _UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
           '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36')

    _TESTS = [{
        'url': 'https://www.threads.net/@tntsportsbr/post/C6cqebdCfBi',
        'info_dict': {
            'id': 'C6cqebdCfBi',
            'ext': 'mp4',
            'uploader_id': 'tntsportsbr',
        },
    }, {
        'url': 'https://www.threads.com/@felipebecari/post/C6cM_yNPHCF',
        'only_matching': True,
    }]

    @staticmethod
    def _shortcode_to_pk(shortcode):
        pk = 0
        for char in shortcode:
            index = _SHORTCODE_ALPHABET.find(char)
            if index < 0:
                raise ExtractorError(f'Invalid Threads shortcode {shortcode!r}')
            pk = pk * 64 + index
        return str(pk)

    def _real_extract(self, url):
        video_id = self._match_id(url)
        pk = self._shortcode_to_pk(video_id)

        webpage = self._download_webpage(
            url, video_id, headers={'User-Agent': self._UA})

        # A logged-in session gets a page with the post's Relay data
        # embedded (result.data.media); anonymous pages are empty shells.
        # Prefer the embedded copy: it is exactly what the browser renders
        # (including quoted posts), and the authenticated form of the
        # GraphQL API has been rejecting requests ("execution error") since
        # 2026-08.
        post = self._find_embedded_post(webpage, video_id, pk)
        if post is None:
            post = self._fetch_post_via_api(url, video_id, pk, webpage)

        formats, thumbnails, metadata, linked_url = self._parse_post(post, video_id)

        if not formats:
            # Quote posts and reposts carry no media of their own; the video
            # belongs to the post they wrap. Threads only reveals that post
            # to logged-in sessions.
            share_info = traverse_obj(post, ('text_post_app_info', 'share_info')) or {}
            for key in ('quoted_attachment_post', 'quoted_post', 'reposted_post'):
                inner = share_info.get(key)
                if not isinstance(inner, dict) or not inner.get('code'):
                    continue
                inner_id = inner['code']
                inner_formats, inner_thumbs, inner_meta, inner_link = self._parse_post(
                    inner, inner_id)
                if inner_formats:
                    self.to_screen(
                        f'{video_id}: {"Repost" if key == "reposted_post" else "Quote"}'
                        f' of {inner_id} - downloading the original post')
                    formats, thumbnails, metadata = inner_formats, inner_thumbs, inner_meta
                    metadata['id'] = inner_id
                    linked_url = inner_link
                    break
                inner_user = traverse_obj(inner, ('user', 'username'))
                if inner_user:
                    # Known post, media not inlined: extract it on its own
                    return self.url_result(
                        f'https://www.threads.com/@{inner_user}/post/{inner_id}',
                        ThreadsIE, inner_id)

        if not formats:
            if linked_url:
                # Don't silently follow the link: extraction on the target
                # site fails often (logins, rate limits) with errors that
                # never mention this was a cross-post. Surface the real
                # location instead; the GUI parses this exact phrasing
                # (CROSSPOST_ERROR_RE in downstream.py) to offer a retry.
                raise ExtractorError(
                    'This Threads post is a cross-post with no video hosted '
                    'on Threads. Download it from the source instead - '
                    f'{_link_source_name(linked_url)}: {linked_url}',
                    expected=True)
            if post.get('media_type') == 19 and not self._logged_in():
                raise ExtractorError(
                    'This Threads post quotes or reposts another post, and '
                    'Threads only shows the original to logged-in users. '
                    'Configure the browser you are logged in to Threads with '
                    '(Settings > Instagram/Threads login) and try again',
                    expected=True)
            self.raise_no_formats(
                'No video found in this Threads post. It may be image/text-only, '
                'deleted, or visible only when logged in - if you are logged in '
                'to Threads in your browser, configure that browser for cookies',
                expected=True)

        metadata.setdefault(
            'title', 'Threads post by {}'.format(
                metadata.get('uploader_id') or 'unknown'))
        metadata['channel'] = metadata.get('uploader_id')
        metadata['channel_url'] = metadata.get('uploader_url')
        metadata['uploader'] = metadata.get('uploader_id')
        metadata['upload_date'] = strftime_or_none(metadata.get('timestamp'))

        return {
            **metadata,
            'formats': formats,
            'thumbnails': thumbnails,
        }

    def _logged_in(self):
        """True when the cookie jar carries a Threads session."""
        return bool(self._get_cookies('https://www.threads.com').get('sessionid'))

    def _find_embedded_post(self, webpage, video_id, pk):
        """The post object from the Relay data logged-in pages embed in
        <script type="application/json"> blocks, or None."""
        def walk(obj):
            if isinstance(obj, dict):
                if ('media_type' in obj and (
                        obj.get('code') == video_id or str(obj.get('pk')) == pk)):
                    return obj
                for value in obj.values():
                    found = walk(value)
                    if found is not None:
                        return found
            elif isinstance(obj, list):
                for value in obj:
                    found = walk(value)
                    if found is not None:
                        return found
            return None

        for block in re.findall(
                r'<script type="application/json"[^>]*>(.*?)</script>',
                webpage, re.DOTALL):
            if video_id not in block or 'media_type' not in block:
                continue
            data = self._parse_json(block, video_id, fatal=False)
            found = walk(data)
            if found is not None:
                self.write_debug('Using post data embedded in the page')
                return found
        return None

    def _fetch_post_via_api(self, url, video_id, pk, webpage):
        """The post object from the BarcelonaPostPageContentQuery API."""
        lsd = self._search_regex(
            r'"LSD",\[\],\{"token":"([^"]+)"', webpage, 'lsd token')

        # Logged-in sessions must send the authenticated request form
        # (fb_dtsg CSRF token, account id in av/__user); the anonymous
        # form with session cookies fails with "Sorry, something went wrong".
        fb_dtsg = self._search_regex(
            r'"DTSGInitialData",\[\],\{"token":"([^"]+)"',
            webpage, 'fb_dtsg token', default=None)
        user_id = self._search_regex(
            r'"(?:ACCOUNT_ID|USER_ID|IG_USER_EIMU)":\s*"(\d{3,})"',
            webpage, 'account id', default=None)
        if not user_id:
            cookie = self._get_cookies('https://www.threads.com').get('ds_user_id')
            user_id = cookie.value if cookie else None

        def query(authenticated, cookiejar=None):
            post_data = {
                'av': user_id if authenticated else '0',
                '__user': user_id if authenticated else '0',
                '__a': '1',
                '__req': '1',
                'dpr': '1',
                'lsd': lsd,
                'fb_api_caller_class': 'RelayModern',
                'fb_api_req_friendly_name': 'BarcelonaPostPageContentQuery',
                'variables': json.dumps({'postID': pk}),
                'server_timestamps': 'true',
                'doc_id': self._GRAPHQL_DOC_ID,
            }
            if authenticated and fb_dtsg:
                post_data['fb_dtsg'] = fb_dtsg
            request = Request(
                'https://www.threads.com/api/graphql',
                data=urlencode_postdata(post_data),
                headers={
                    'User-Agent': self._UA,
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-FB-LSD': lsd,
                    'X-IG-App-ID': '238260118697367',
                    'X-ASBD-ID': '129477',
                    'X-FB-Friendly-Name': 'BarcelonaPostPageContentQuery',
                    'Origin': 'https://www.threads.com',
                    'Referer': url,
                    'Accept': '*/*',
                    # Meta rejects the request (error 1357055) without these
                    'Sec-Fetch-Site': 'same-origin',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Dest': 'empty',
                })
            if cookiejar is not None:
                request.extensions['cookiejar'] = cookiejar
            return self._download_json(
                request, video_id,
                note='Downloading post JSON' + (
                    ' (anonymously)' if cookiejar is not None else ''),
                # Error responses carry an anti-JSON-hijacking prefix
                transform_source=lambda s: s.removeprefix('for (;;);'))

        authenticated = bool(fb_dtsg and user_id)
        response = query(authenticated)
        if authenticated and response.get('data') is None:
            # The authenticated form is rejected outright at the moment.
            # Public posts don't need the login: retry with an empty cookie
            # jar (session cookies + anonymous form is rejected too).
            self.report_warning(
                'Threads rejected the logged-in request ({}); retrying '
                'anonymously'.format(
                    traverse_obj(response, ('errors', 0, 'message'))
                    or traverse_obj(response, 'errorSummary')
                    or response.get('error') or 'no details'),
                video_id, only_once=True)
            response = query(False, cookiejar=YoutubeDLCookieJar())

        if traverse_obj(response, 'error'):
            raise ExtractorError(
                'Threads API error: {}'.format(
                    traverse_obj(response, 'errorSummary') or response['error']),
                expected=True)

        # Relay failures come back as errors=[{message, severity}, ...] with
        # data=null (note the plural key, unlike the platform-level 'error'
        # above). Seen for deleted and login-restricted posts.
        if response.get('data') is None:
            raise ExtractorError(
                'Threads reports this post as unavailable ({}) - it may be '
                'deleted or restricted to logged-in users. If you can open it '
                'in your browser, configure that browser for cookies '
                '(Settings > Instagram/Threads login)'.format(
                    traverse_obj(response, ('errors', 0, 'message'))
                    or 'no details'),
                expected=True)

        # The response carries the whole thread (post + replies); pick ours
        for node in traverse_obj(response, ('data', 'data', 'edges')) or []:
            for item in traverse_obj(node, ('node', 'thread_items')) or []:
                post = item.get('post')
                if post and (str(post.get('pk')) == pk
                             or post.get('code') == video_id):
                    return post
        raise ExtractorError(
            'Threads returned the thread without this post - it may have '
            'been deleted', expected=True)

    @staticmethod
    def _parse_post(post, video_id):
        """(formats, thumbnails, metadata, linked_url) for one post object."""
        formats = []
        thumbnails = []
        metadata = {'id': video_id}

        # Cross-posts / link-share posts (media_type 19) have no media of
        # their own; the video lives behind the shared link (commonly an
        # Instagram reel)
        linked_url = traverse_obj(post, (
            'text_post_app_info', 'link_preview_attachment', 'url'))

        # Carousel posts carry several media items; plain posts are their
        # own single media item
        for media in post.get('carousel_media') or [post]:
            for video in media.get('video_versions') or []:
                if not video.get('url'):
                    continue
                formats.append({
                    'format_id': '{}-{}'.format(
                        media.get('pk'), video.get('type')),
                    'url': video['url'],
                    'ext': 'mp4',
                    'width': media.get('original_width'),
                    'height': media.get('original_height'),
                })

        for thumb in traverse_obj(
                post, ('image_versions2', 'candidates')) or []:
            if not thumb.get('url'):
                continue
            thumbnails.append({
                'url': thumb['url'],
                'width': thumb.get('width'),
                'height': thumb.get('height'),
            })

        username = traverse_obj(post, ('user', 'username'))
        caption = traverse_obj(post, ('caption', 'text'))
        metadata['uploader_id'] = username
        metadata['channel_is_verified'] = traverse_obj(post, ('user', 'is_verified'))
        if username:
            metadata['uploader_url'] = f'https://www.threads.com/@{username}'
        metadata['timestamp'] = post.get('taken_at')
        metadata['like_count'] = post.get('like_count')
        if caption:
            metadata['title'] = caption
            metadata['description'] = caption

        return formats, thumbnails, metadata, linked_url


class ThreadsShareIE(InfoExtractor):
    IE_NAME = 'threads:share'
    IE_DESC = 'Threads share links (threads.com/share/CODE)'
    _VALID_URL = r'https?://(?:www\.)?threads\.(?:net|com)/share/(?P<id>[^/?#&]+)'
    _TESTS = [{
        'url': 'https://www.threads.com/share/GR2RkcluE/',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        share_id = self._match_id(url)
        # With a full browser User-Agent Meta serves a JS shell that
        # resolves the share code client-side; with a plain client UA the
        # server answers 302 straight to the canonical post URL. The
        # redirect must NOT be followed - fetching the target with a
        # non-browser UA bounces to /?error=invalid_post - and yt-dlp's
        # networking always follows redirects, so this one request goes
        # through urllib directly (proxy settings are not applied to it).
        self.to_screen(f'{share_id}: Resolving share link')

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        req = urllib.request.Request(url, headers={'User-Agent': 'curl/8.0'})
        location = None
        try:
            urllib.request.build_opener(NoRedirect).open(req, timeout=20)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get('Location')
            else:
                raise ExtractorError(
                    f'Threads share link returned HTTP {e.code}', expected=True)
        if not location or '/share/' in location or 'error=' in location:
            raise ExtractorError(
                'Could not resolve Threads share link - the post may have '
                'been deleted', expected=True)
        return self.url_result(urllib.parse.urljoin(url, location), ThreadsIE)


class ThreadsIOSIE(InfoExtractor):
    IE_NAME = 'threads:ios'
    IE_DESC = "Threads' iOS barcelona:// URL"
    _VALID_URL = r'barcelona://media\?shortcode=(?P<id>[^/?#&]+)'
    _TESTS = [{
        'url': 'barcelona://media?shortcode=C6fDehepo5D',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        video_id = self._match_id(url)
        # Threads ignores the username segment and redirects to the right
        # post, so a placeholder works
        return self.url_result(
            f'https://www.threads.com/@_/post/{video_id}', ThreadsIE, video_id)
