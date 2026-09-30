# Chromium HTTP navigation diagnosis

**Finding:** Chromium's default Linux password-store path stalls HTTP navigation before request dispatch. The smallest verified QA-only fix is adding `--password-store=basic` to the arguments of the owned, disposable Chromium profile. No application, CSS, GPU, dependency, or omowright-runtime change is indicated.

**Controlled proof:** Two sequential fresh `/tmp/edt-map-diagnosis-*` profiles, identical Chromium binary, omowright library, viewport, URL, and flags. Default store: `Page.navigate` times out after 5004 ms. With the sole additional flag `--password-store=basic`: navigation completes after 142 ms and Runtime.evaluate returns the expected local app URL/title. A previous independent basic-store probe also received HTTP 200 and completed in 135 ms. Raw output is preserved below.

**Boundary evidence:** `curl --max-time 5 -s -o /dev/null -w 'HTTP %{http_code} time %{time_total}\n' http://127.0.0.1:8080/` returned HTTP 200 in 0.000245 seconds. The same owned CDP session immediately navigated a data URL but stalled on HTTP. Chromium netlog showed DIRECT proxy resolution, no HSTS upgrade, completion of network delegate and first-party-set metadata work, then `COMPUTED_PRIVACY_MODE` with no HTTP headers sent before cancellation. Disabling extensions did not fix it. One browser network-service process did establish a preconnect socket to port 8080, so absence of a TCP connection is not an invariant; the blocked HTTP request is the relevant boundary.

**Interpretation limit:** The A/B test directly proves dependence on Chromium's password-store backend. The associated asynchronous cookie/OS credential-store initialization is the likely internal wait, consistent with the netlog boundary; this probe did not trace the underlying system keyring implementation or prove why it became unresponsive. Do not describe it as a confirmed GPU or CDP bug.

**Source inspection:** omowright `PipeCdpClient` launches the given binary/args plus `--remote-debugging-pipe`. Frame initialization uses `waitForDebuggerOnStart:false`. Request interception (`Fetch.enable`) is only enabled by `createRoutes().route()` and was not used here. No runtime edits were made.

## Reproduce the controlled comparison

Invocation: `node --input-type=module` with the following script from the repository root. PASS is successful Page.navigate followed by the app's title; FAIL is a 5-second CDP command timeout.

```js
import {mkdtempSync, rmSync} from 'node:fs';
import {loadOmowright} from '/home/user/.codex/plugins/cache/sisyphuslabs/omo/5.1.1/skills/browser/scripts/omowright.mjs';
const {omowright:o} = await loadOmowright();
for (const basic of [false, true]) {
  const profile = mkdtempSync('/tmp/edt-map-diagnosis-');
  const browser = await o.connectPipe({
    browserPath:'/home/user/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome',
    browserArgs:['--headless','--no-sandbox','--disable-gpu','--no-first-run',
      ...(basic ? ['--password-store=basic'] : []), '--user-data-dir='+profile],
    storageRoot:profile, commandTimeoutMs:5000,
  });
  try {
    const page = (await o.createAgentTabs(browser).create('about:blank')).page;
    const session = await page.resolveSessionId();
    try {
      await page.cdp.send('Page.navigate',{url:'http://127.0.0.1:8080/'},session);
      console.log(basic, 'PASS', await page.cdp.send('Runtime.evaluate',
        {expression:'document.title',returnByValue:true},session));
    } catch (error) { console.log(basic,'FAIL',error.message); }
    await page.cdp.send('Page.stopLoading',{},session);
  } finally { await browser.close(); rmSync(profile,{recursive:true,force:true}); }
}
```

All diagnostic browser instances were closed and their owned profiles removed in `finally`. No user's browser/profile was attached. This evidence file is the only persistent file written by this diagnostic assignment.

## Isolated extensions-disabled probe

```text
FLAGS --disable-extensions --headless --no-sandbox --disable-gpu --no-first-run --no-proxy-server
TARGETS [{"type":"service_worker","url":"chrome-extension://nkeimhogjdpnpccoofpliimaahmaaome/thunk.js"},{"type":"page","url":"about:blank"},{"type":"page","url":"chrome://newtab/"}]
LOCAL FAIL CDP command timeout: Page.navigate
LOCAL NETLOG [{"type":"HTTP_STREAM_JOB_CONTROLLER","phase":1,"params":{"allowed_bad_certs":[],"is_preconnect":true,"privacy_mode":"disabled","url":"http://127.0.0.1:8080/"}},{"type":"PROXY_RESOLUTION_SERVICE","phase":1},{"type":"PROXY_RESOLUTION_SERVICE_RESOLVED_PROXY_LIST","phase":0,"params":{"proxy_info":"DIRECT"}},{"type":"PROXY_RESOLUTION_SERVICE","phase":2},{"type":"HTTP_STREAM_JOB_CONTROLLER_PROXY_SERVER_RESOLVED","phase":0,"params":{"proxy_chain":"[direct://]"}},{"type":"HTTP_STREAM_JOB_CONTROLLER","phase":2},{"type":"CORS_REQUEST","phase":1,"params":{"cors_preflight_policy":"consider_preflight","is_revalidating":false,"request_headers":{"headers":["sec-ch-ua: \"Chromium\";v=\"151\", \"Not=A?Brand\";v=\"99\"","sec-ch-ua-mobile: ?0","sec-ch-ua-platform: \"Linux\"","Accept-Language: en-US,en;q=0.9","Upgrade-Insecure-Requests: 1","User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/151.0.0.0 Safari/537.36","Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"],"line":"GET / HTTP/1.1\r\n"},"url":"http://127.0.0.1:8080/"}},{"type":"CHECK_CORS_PREFLIGHT_REQUIRED","phase":0,"params":{"preflight_required":false}},{"type":"REQUEST_ALIVE","phase":1,"params":{"priority":"HIGHEST","traffic_annotation":63171670,"url":"http://127.0.0.1:8080/"}},{"type":"NETWORK_DELEGATE_BEFORE_URL_REQUEST","phase":1},{"type":"NETWORK_DELEGATE_BEFORE_URL_REQUEST","phase":2},{"type":"TRANSPORT_SECURITY_STATE_SHOULD_UPGRADE_TO_SSL","phase":0,"params":{"get_sts_state_result":false,"host":"127.0.0.1","host_found_in_hsts_bypass_list":false,"should_upgrade_to_ssl":false}},{"type":"URL_REQUEST_START_JOB","phase":1,"params":{"initiator":"not an origin","load_flags":98560,"method":"GET","network_isolation_key":"http://127.0.0.1 http://127.0.0.1","request_type":"main frame","site_for_cookies":"SiteForCookies: {site=http://127.0.0.1; schemefully_same=true}","url":"http://127.0.0.1:8080/"}},{"type":"FIRST_PARTY_SETS_METADATA","phase":1},{"type":"FIRST_PARTY_SETS_METADATA","phase":2,"params":{"cache_filter":"none","frame_entry":"none","top_frame_primary":"none"}},{"type":"COMPUTED_PRIVACY_MODE","phase":0,"params":{"privacy_mode":"disabled"}},{"type":"HTTP_STREAM_JOB_CONTROLLER","phase":1,"params":{"allowed_bad_certs":[],"is_preconnect":true,"privacy_mode":"disabled","url":"http://127.0.0.1:8080/"}},{"type":"PROXY_RESOLUTION_SERVICE","phase":1},{"type":"PROXY_RESOLUTION_SERVICE_RESOLVED_PROXY_LIST","phase":0,"params":{"proxy_info":"DIRECT"}},{"type":"PROXY_RESOLUTION_SERVICE","phase":2},{"type":"HTTP_STREAM_JOB_CONTROLLER_PROXY_SERVER_RESOLVED","phase":0,"params":{"proxy_chain":"[direct://]"}},{"type":"HTTP_STREAM_JOB_CONTROLLER","phase":2},{"type":"CORS_REQUEST","phase":2},{"type":"CANCELLED","phase":0},{"type":"REQUEST_ALIVE","phase":2}]
CLEANED /tmp/edt-map-diagnosis-Cxfn5h
```

## Basic password store probe

```text
FLAGS ["--headless","--no-sandbox","--disable-gpu","--no-first-run","--password-store=basic","--user-data-dir=/tmp/edt-map-diagnosis-UjcosZ"]
RESPONSE 200 http://127.0.0.1:8080/
LOCAL NAV {"frameId":"C5CA42877207EF64D7A4D4440C02E517","loaderId":"AEA8F82ADF64384AB20BB6CCD88FE8F0","isDownload":false}
ELAPSED_MS 135
DOM {"result":{"type":"object","value":{"url":"http://127.0.0.1:8080/","title":"탐라–한림 · 에너지 지도","body":true}}}
CLEANED /tmp/edt-map-diagnosis-UjcosZ
```

## Controlled A/B: only password-store flag differs

```text
CASE default store
NAV FAIL 5004 CDP command timeout: Page.navigate
CLEANED /tmp/edt-map-diagnosis-TmkynD
CASE basic store
NAV PASS 142
DOM {"url":"http://127.0.0.1:8080/","title":"탐라–한림 · 에너지 지도"}
CLEANED /tmp/edt-map-diagnosis-enZZJ3
```
