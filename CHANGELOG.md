# Changelog

All notable changes to Buoy are documented here.

## Unreleased

### Added
- Per-peer latency history sparkline in fleet view (#44)
- Favicon set for buoy dashboard (#20)
- WebSocket reconnect status banner (#46)
- Keyboard shortcuts for panel navigation (#47)
- Improved mobile responsive breakpoints (#48)
- 24h uptime history sparkline per container (#49)
- systemd_health built-in plugin for service health monitoring (#50)
- System journal error count gauge (#45)
- Internet speedtest tracker plugin (#51)
- SnapRAID parity status plugin (#53)
- Tailscale network status plugin (#54)
- Immich photo library stats plugin (#56)
- Jellyfin media server status plugin (#57)
- Proxmox VE status plugin (#58)
- WireGuard tunnel status plugin (#59)
- Portainer remote container stats plugin (#60)
- Generic SMART disk health plugin (SATA + NVMe) (#61)
- Actual Budget monthly summary plugin (#62)
- Docker image update checker with per-container badges (#63)
- Auto-discover built-in plugins via pkgutil (#64)
- dns_filter plugin for Pi-hole / AdGuard Home (#65)
- Trigger.dev task run status plugin (#66)
- TLS certificate expiry tracking (cert_expiry plugin) (#69)
- `--dev` flag for hot-reload and debug logging (#68)
- Auth-protected `/api/config/debug` endpoint (#67)
- Env-based plugin secrets (`BUOY_PLUGIN_<ID>_<KEY>`) (#72)
- Cross-node alert forwarding in fleet view (#74)
- Theme: persist toggle, `prefers-color-scheme` detection, and new presets (#216)

### Fixed
- Content-Security-Policy header on all responses (SEC-6); removed remaining inline `onclick` handlers to keep `script-src` free of `unsafe-inline` (#81)
- `Plugin.config` moved to per-instance `__init__` to prevent shared state (#207)
- `theme.custom` CSS variable overrides now applied at page load (#150)
- Rate limiting now always active, independent of `auth.enabled` (#213)
- Doubled `%` on container CPU stat in detail panel (#149)
- Auth now fails closed when enabled without credentials (SEC-1) (#214)
- Escaped untrusted strings before `innerHTML` (SEC-3, stored XSS) (#212)
- Same-origin CORS by default; opt-in allowlist for fleet peers (#211)
- `/api/config/debug` gated independently of `auth.enabled` (#208)
- Prometheus collector: escape label values and use exact uptime from `/proc/uptime` (#209)
- Plugin refresh: honour `refresh.plugins_interval` as server-side collection floor (#210)
- `speedtest-cli` made an optional dependency (#151)
- Docker: cache container list and reuse collector across `/api/stats` (#220)
- Frontend: correct `formatUptime` boundary and extract shared util (#218)
- Alerts: webhook dispatch reads URL from `config.alerts` not `plugins.builtin` (#217)

## [2.3.0](https://github.com/gfargo/buoy/compare/buoy-v2.2.1...buoy-v2.3.0) (2026-08-30)


### Features

* **fleet:** show peer latency badge in fleet grid without history ([#290](https://github.com/gfargo/buoy/issues/290)) ([58556b6](https://github.com/gfargo/buoy/commit/58556b668ebb862a60218b0685f85b3a82348311))


### Bug Fixes

* **dns_filter:** add Pi-hole v6 session auth with v5/v6 auto-detection ([#291](https://github.com/gfargo/buoy/issues/291)) ([71c340b](https://github.com/gfargo/buoy/commit/71c340b822505b2cbf3a6aac079ab9c6a07f53f1))

## [2.2.1](https://github.com/gfargo/buoy/compare/buoy-v2.2.0...buoy-v2.2.1) (2026-08-28)


### Bug Fixes

* **alerts:** escalate warnings to critical ([#288](https://github.com/gfargo/buoy/issues/288)) ([b39c7a0](https://github.com/gfargo/buoy/commit/b39c7a085eb6abc2ed2f3ea258ca84bd81498e5d))
* **auth:** bound rate-limit client state ([#283](https://github.com/gfargo/buoy/issues/283)) ([132b431](https://github.com/gfargo/buoy/commit/132b43132e5768e5f30f8c1e07210aba0a123c9d))
* **auth:** harden trusted proxy client resolution ([#286](https://github.com/gfargo/buoy/issues/286)) ([6703520](https://github.com/gfargo/buoy/commit/67035203a4d662a60180425071db1a7cc06613ab))
* **auth:** support authenticated frontend actions ([#285](https://github.com/gfargo/buoy/issues/285)) ([bf499b2](https://github.com/gfargo/buoy/commit/bf499b27147ed86a626d50c7c07a7a92ce1991d1))
* **ci:** ignore pre-release compare links ([#292](https://github.com/gfargo/buoy/issues/292)) ([c0b8197](https://github.com/gfargo/buoy/commit/c0b81975315cc2a44a362c30b72a41c436338e60))
* **ci:** match release-please's actual tag format in Release workflow ([#278](https://github.com/gfargo/buoy/issues/278)) ([aed84ea](https://github.com/gfargo/buoy/commit/aed84ea59eaa327b18b7388c5f15b525e1327d01))
* **config:** stop _parse_plugins from mutating the caller's raw dict ([#276](https://github.com/gfargo/buoy/issues/276)) ([c73815a](https://github.com/gfargo/buoy/commit/c73815a286983dc58e8c7c713691a9db07dd63d5))
* **network:** add verify_ssl to fleet peer polling, replacing hardcoded verify=False ([#280](https://github.com/gfargo/buoy/issues/280)) ([3e64061](https://github.com/gfargo/buoy/commit/3e64061dc313acf5af50170474380b91f68ed450))
* **plugins:** harden lifecycle and user configuration ([#284](https://github.com/gfargo/buoy/issues/284)) ([5ace982](https://github.com/gfargo/buoy/commit/5ace98228e7f9fb420e0c2efa2884a6335264f80))
* **plugins:** stub every plugin in demo mode instead of calling collect() ([#273](https://github.com/gfargo/buoy/issues/273)) ([78b33f4](https://github.com/gfargo/buoy/commit/78b33f4b7e17452608d3dc69aea59c439cc7f47c)), closes [#97](https://github.com/gfargo/buoy/issues/97)
* **server:** decouple history collection from websocket transport ([#275](https://github.com/gfargo/buoy/issues/275)) ([f24bfe1](https://github.com/gfargo/buoy/commit/f24bfe100bca8a61d1abb8d68f75b996837b677b))
* **server:** isolate application runtime state ([#287](https://github.com/gfargo/buoy/issues/287)) ([b398216](https://github.com/gfargo/buoy/commit/b398216e494b3986319300291534451761ab2256))
* **storage:** serialize close with database operations ([#282](https://github.com/gfargo/buoy/issues/282)) ([0874ba1](https://github.com/gfargo/buoy/commit/0874ba14c762d99d2dcec703b10f527809a4438c))
* **subprocess:** bound timeout cleanup ([#281](https://github.com/gfargo/buoy/issues/281)) ([bb58ffb](https://github.com/gfargo/buoy/commit/bb58ffbdec08c43fedfb98e65ce64a17cb1ed392))
* support reverse-proxy sub-path hosting (BUG-45) ([#274](https://github.com/gfargo/buoy/issues/274)) ([55411a8](https://github.com/gfargo/buoy/commit/55411a81220264a9e5fe06d9e2355349a5d32e93))
* **version:** unify application version reporting ([#289](https://github.com/gfargo/buoy/issues/289)) ([b2cec09](https://github.com/gfargo/buoy/commit/b2cec094eea86fb57754317eab8bce5fe58c4bd9))
* **ws:** iterate a snapshot when broadcasting to avoid RuntimeError ([#272](https://github.com/gfargo/buoy/issues/272)) ([ebde0a2](https://github.com/gfargo/buoy/commit/ebde0a2b39fbb5563526c27b7a31e08d80bffde3))

## [2.2.0](https://github.com/gfargo/buoy/compare/buoy-v2.1.0...buoy-v2.2.0) (2026-08-23)


### Features

* add Zigbee2MQTT plugin for coordinator status + link quality ([#270](https://github.com/gfargo/buoy/issues/270)) ([3ac2ba0](https://github.com/gfargo/buoy/commit/3ac2ba0de6fda92f9b52f2156c40c36fef461acb)), closes [#269](https://github.com/gfargo/buoy/issues/269)
* **api:** add auth-protected /api/config/debug endpoint ([#67](https://github.com/gfargo/buoy/issues/67)) ([3fa740d](https://github.com/gfargo/buoy/commit/3fa740d795c9aae919ee506a4b842482d38c8c0a))
* container detail panel with metadata + controls ([57e337d](https://github.com/gfargo/buoy/commit/57e337d99bb90cf062195cf16bcd069b8577e725))
* container detail panel with metadata, logs, and restart ([#1](https://github.com/gfargo/buoy/issues/1)) ([eb13784](https://github.com/gfargo/buoy/commit/eb1378487c13c2fc71f50371ed1a940d27a7e908))
* **containers:** 24h uptime history sparkline ([#49](https://github.com/gfargo/buoy/issues/49)) ([bf387bf](https://github.com/gfargo/buoy/commit/bf387bf372efbbd76ef124a04b2f4c5a4434f679))
* **css:** improve mobile responsive breakpoints ([#48](https://github.com/gfargo/buoy/issues/48)) ([c3d4776](https://github.com/gfargo/buoy/commit/c3d477652ad4e736573d2ff1228fdec8bea51f4b))
* deploy info in footer — version, build date, git SHA ([#8](https://github.com/gfargo/buoy/issues/8)) ([dd78101](https://github.com/gfargo/buoy/commit/dd7810152583d9dd9c0aa08c88747b899813a534))
* deploy info in footer (version + build date + git SHA) ([46ca99e](https://github.com/gfargo/buoy/commit/46ca99ebc31564127ef3841f3d49770d66f10bec))
* **dev:** add --dev flag for hot-reload and debug logging ([#68](https://github.com/gfargo/buoy/issues/68)) ([c342ad4](https://github.com/gfargo/buoy/commit/c342ad4669ee0da0376ebe976ecc686ecf735564))
* **docker:** add image update checker with per-container badges ([#63](https://github.com/gfargo/buoy/issues/63)) ([ed7841f](https://github.com/gfargo/buoy/commit/ed7841f7a12deb331adab3362ad88e6d16215d67))
* **fleet:** cross-node alert forwarding in fleet view ([#74](https://github.com/gfargo/buoy/issues/74)) ([a05ad90](https://github.com/gfargo/buoy/commit/a05ad90997921e497f1e7f82d138797b7fb0cad8))
* **fleet:** per-peer latency history sparkline ([#44](https://github.com/gfargo/buoy/issues/44)) ([4fdc635](https://github.com/gfargo/buoy/commit/4fdc6354669050183fc533291beddc3036dafb49))
* **fleet:** show service link pills on fleet node cards ([a1c41e8](https://github.com/gfargo/buoy/commit/a1c41e85af3ffad288988559eeb5125a0528350b))
* **fleet:** show service link pills on fleet node cards ([c13fef5](https://github.com/gfargo/buoy/commit/c13fef5154028b3aea6c0731b8227f25391f88fd)), closes [#6](https://github.com/gfargo/buoy/issues/6)
* initial release — buoy v2.0.0-alpha.1 ([a0ecab5](https://github.com/gfargo/buoy/commit/a0ecab5ad3dc5abf1c812ce1370164ec9314ba57))
* **network:** use tailscale ping for peer latency ([ab923d1](https://github.com/gfargo/buoy/commit/ab923d19de0314b4949e56a70c6f5591528c7a92))
* **network:** use tailscale ping for peer latency with HTTP fallback ([3377713](https://github.com/gfargo/buoy/commit/33777137e1f2be51cf0e2ebb0773562244a7ac9f))
* **plugins:** add Actual Budget monthly summary plugin ([#62](https://github.com/gfargo/buoy/issues/62)) ([ab1a276](https://github.com/gfargo/buoy/commit/ab1a276c0f3f904f49b21d312c74da9240f4c74d))
* **plugins:** add dns_filter plugin for Pi-hole / AdGuard Home ([#65](https://github.com/gfargo/buoy/issues/65)) ([99365e9](https://github.com/gfargo/buoy/commit/99365e9738df297849cebf889f096e38b17b94f2))
* **plugins:** add Home Assistant plugin ([#254](https://github.com/gfargo/buoy/issues/254)) ([04a823c](https://github.com/gfargo/buoy/commit/04a823c7cfd46f9679aff4087f623634bbefa3b1))
* **plugins:** add Immich photo library stats plugin ([#56](https://github.com/gfargo/buoy/issues/56)) ([aa105ee](https://github.com/gfargo/buoy/commit/aa105ee5d90dac223566f6b5d3b26affb1401e76))
* **plugins:** add internet speedtest tracker plugin ([#51](https://github.com/gfargo/buoy/issues/51)) ([9949bb3](https://github.com/gfargo/buoy/commit/9949bb3310905bec12d651de5445e67225863862))
* **plugins:** add Jellyfin media server status plugin ([#57](https://github.com/gfargo/buoy/issues/57)) ([8a3ffc3](https://github.com/gfargo/buoy/commit/8a3ffc32bcdf40cd6f333c12e44e036486b79357))
* **plugins:** add Portainer remote container stats plugin ([#60](https://github.com/gfargo/buoy/issues/60)) ([ce307c5](https://github.com/gfargo/buoy/commit/ce307c570a6d6173984a9061b994630b06c6364a))
* **plugins:** add Proxmox VE status plugin ([#58](https://github.com/gfargo/buoy/issues/58)) ([5f0752a](https://github.com/gfargo/buoy/commit/5f0752a10b76933875360b289884c297b4b7ac42))
* **plugins:** add SnapRAID parity status plugin ([#53](https://github.com/gfargo/buoy/issues/53)) ([231cc04](https://github.com/gfargo/buoy/commit/231cc04e6e7b54370081376fd8b3fa22b36e1215))
* **plugins:** add systemd_health built-in plugin ([#50](https://github.com/gfargo/buoy/issues/50)) ([75a4177](https://github.com/gfargo/buoy/commit/75a4177135e9c2a0ca516c5af6d0cfe419c4e4d4))
* **plugins:** add Tailscale network status plugin ([#54](https://github.com/gfargo/buoy/issues/54)) ([a8c8f5b](https://github.com/gfargo/buoy/commit/a8c8f5b49c0fa466e87196896dbdb40e060a20bf))
* **plugins:** add Trigger.dev task run status plugin ([#66](https://github.com/gfargo/buoy/issues/66)) ([6c0592a](https://github.com/gfargo/buoy/commit/6c0592ada2dfbee7e8b3c1fcafc9ecb9a9adadaf))
* **plugins:** allow per-plugin refresh interval override in config ([#238](https://github.com/gfargo/buoy/issues/238)) ([a6c6470](https://github.com/gfargo/buoy/commit/a6c64707a3f7a7aa68efec7f70e3215b2b371d48))
* **plugins:** auto-discover built-in plugins via pkgutil ([#64](https://github.com/gfargo/buoy/issues/64)) ([4a12718](https://github.com/gfargo/buoy/commit/4a127187ea3efd137899db747244508d8f2b08b4))
* **plugins:** env-based plugin secrets (BUOY_PLUGIN_&lt;ID&gt;_&lt;KEY&gt;) ([#72](https://github.com/gfargo/buoy/issues/72)) ([ab5d776](https://github.com/gfargo/buoy/commit/ab5d77645284d44192e840a843510f56c956025b))
* **plugins:** expose plugin health and loaded state via /api/plugins ([#235](https://github.com/gfargo/buoy/issues/235)) ([69f4bf7](https://github.com/gfargo/buoy/commit/69f4bf7bf28a89b5df9b1d810a3a01726b8c7b68))
* **plugins:** generic SMART disk health (SATA + NVMe) ([#61](https://github.com/gfargo/buoy/issues/61)) ([543c8b4](https://github.com/gfargo/buoy/commit/543c8b4eeba1847e29f16ee2c18281804bf07434))
* **plugins:** let user plugins receive configuration ([#237](https://github.com/gfargo/buoy/issues/237)) ([726349c](https://github.com/gfargo/buoy/commit/726349c2dd63dda4495bc43f82d86e6737f5b7a1))
* **plugins:** replace new Function()+raw-HTML rendering with declarative panel spec ([#241](https://github.com/gfargo/buoy/issues/241)) ([08ae55a](https://github.com/gfargo/buoy/commit/08ae55a1fc010e3e8b0dd4f0e514e9a1a0a6e50c))
* **plugins:** support plugin distribution via entry points + buoy plugin CLI ([#233](https://github.com/gfargo/buoy/issues/233)) ([f36b642](https://github.com/gfargo/buoy/commit/f36b642ba950a5589cda1b260dc9e7bb57868e61))
* **plugins:** system journal error count gauge ([#45](https://github.com/gfargo/buoy/issues/45)) ([abd1dd6](https://github.com/gfargo/buoy/commit/abd1dd63df5ececffc48919ff9a145f644b2c4cf))
* **plugins:** TLS certificate expiry tracking (cert_expiry plugin) ([#69](https://github.com/gfargo/buoy/issues/69)) ([6aa529c](https://github.com/gfargo/buoy/commit/6aa529c40ca8b7255ff5b40bc3f1bd6094725107))
* **plugins:** WireGuard tunnel status plugin ([#59](https://github.com/gfargo/buoy/issues/59)) ([13daa1c](https://github.com/gfargo/buoy/commit/13daa1c594010a8a1820913fa08c38c139bb7940))
* **theme:** persist toggle, prefers-color-scheme, new presets ([#216](https://github.com/gfargo/buoy/issues/216)) ([bdb35c3](https://github.com/gfargo/buoy/commit/bdb35c34fbd368587d686061c82f25ec3deb94fc))
* **ui:** add favicon set to buoy dashboard ([#20](https://github.com/gfargo/buoy/issues/20)) ([ca88882](https://github.com/gfargo/buoy/commit/ca88882f87b0a3226c7aca76ed25abda35cca058))
* **ui:** keyboard shortcuts for navigation ([#47](https://github.com/gfargo/buoy/issues/47)) ([dd02866](https://github.com/gfargo/buoy/commit/dd0286675e04ce53576e06809a77e2012b182c2e))
* **ws:** WebSocket reconnect status banner ([#46](https://github.com/gfargo/buoy/issues/46)) ([0bc548b](https://github.com/gfargo/buoy/commit/0bc548b45612aa6dddd3a3c74b2e3bd355f58cff))


### Bug Fixes

* **alerts:** repair webhook dispatch — read URL from config.alerts not plugins.builtin ([#217](https://github.com/gfargo/buoy/issues/217)) ([31cf16a](https://github.com/gfargo/buoy/commit/31cf16a8e4d648dbf5973349bfaf768fb98c489b))
* **alerts:** use async httpx client for webhook dispatch ([#265](https://github.com/gfargo/buoy/issues/265)) ([27649db](https://github.com/gfargo/buoy/commit/27649db02a974e932f27cd46d754b704c0f411d8))
* **auth:** fail closed when auth is enabled without credentials (SEC-1) ([#214](https://github.com/gfargo/buoy/issues/214)) ([16de6ce](https://github.com/gfargo/buoy/commit/16de6ce34d0a6f78b5c5fcad1302321783d1c3c9))
* **auth:** make rate limiting always active, independent of auth.enabled ([#213](https://github.com/gfargo/buoy/issues/213)) ([f6c1804](https://github.com/gfargo/buoy/commit/f6c180414cc5cfeb4185fbf1b4941b84c7a54224))
* **config:** guard int() cast on env overrides ([#267](https://github.com/gfargo/buoy/issues/267)) ([5d861ef](https://github.com/gfargo/buoy/commit/5d861eff1b30a0b4676c9ac52c1b3091bd2c5e1b))
* **css:** define --red-dim in all themes; map undefined var aliases to real palette ([#223](https://github.com/gfargo/buoy/issues/223)) ([af2d9f3](https://github.com/gfargo/buoy/commit/af2d9f3c1fbce0bb3530c25fcd70a63c6eddc5c9))
* **detail:** remove doubled % on container CPU stat ([#149](https://github.com/gfargo/buoy/issues/149)) ([5efc9cc](https://github.com/gfargo/buoy/commit/5efc9ccd9b9f80d4d66df986379fd3e1716ca2f5))
* **docker:** cache container list and reuse collector across /api/stats ([#220](https://github.com/gfargo/buoy/issues/220)) ([205bf92](https://github.com/gfargo/buoy/commit/205bf92cf671588f2ee0c32c515ae5dc34c7c27e))
* Dockerfile build order — copy src before pip install ([f73feb5](https://github.com/gfargo/buoy/commit/f73feb514ac63bd2730c5dd74a81e6e62585f72f))
* **frontend:** correct formatUptime boundary and extract shared util ([#218](https://github.com/gfargo/buoy/issues/218)) ([ccabd4b](https://github.com/gfargo/buoy/commit/ccabd4b49e23eadf7471b1189568c47d17eb5fb2))
* **frontend:** match confirm-restart button copy to spec wording ([#232](https://github.com/gfargo/buoy/issues/232)) ([36224cb](https://github.com/gfargo/buoy/commit/36224cb288a7f3047911f94ebafeead8726306e2))
* **frontend:** render node.role in header ([#229](https://github.com/gfargo/buoy/issues/229)) ([668bd40](https://github.com/gfargo/buoy/commit/668bd40dc78dd3486d378f7c79db4457aabef3da))
* **frontend:** render node.tier in tier badge ([#224](https://github.com/gfargo/buoy/issues/224)) ([27aa467](https://github.com/gfargo/buoy/commit/27aa467da7cb032289a155e387c6c8708759f328))
* **frontend:** suppress stats polling while WebSocket is open ([#222](https://github.com/gfargo/buoy/issues/222)) ([a995bf9](https://github.com/gfargo/buoy/commit/a995bf92a1a540579b0e8b5302ca7102bae4c077))
* **frontend:** use correct field names for container Started/Image Age ([#230](https://github.com/gfargo/buoy/issues/230)) ([5790db6](https://github.com/gfargo/buoy/commit/5790db65acb7096e2aa4c5733acc9f2dbe2c5dac))
* include README.md in Docker build for hatchling metadata ([4940102](https://github.com/gfargo/buoy/commit/4940102ab3316754594f1bb842f86218c71a4d67))
* install zigbee2mqtt extra in the shipped Docker image ([#271](https://github.com/gfargo/buoy/issues/271)) ([8847f4c](https://github.com/gfargo/buoy/commit/8847f4c3027c8ab3140e7c9dac600028490b9671))
* **logging:** adopt structured logging, stop swallowing exceptions silently ([#261](https://github.com/gfargo/buoy/issues/261)) ([0d674a4](https://github.com/gfargo/buoy/commit/0d674a44c44c7346c6fd2404a0f9ee05618fd680))
* **metrics:** gate /metrics on prometheus_exporter plugin; add rate-limit and auth ([#228](https://github.com/gfargo/buoy/issues/228)) ([a537160](https://github.com/gfargo/buoy/commit/a537160e586ee9bf99f2876a45c9f6ce51feba93))
* **nvme:** use nsenter for smartctl in container, update health badge ([4df1d22](https://github.com/gfargo/buoy/commit/4df1d22ccdca0575aa6461951529a96c86a0e0b1))
* **nvme:** use nsenter for smartctl in container, update health badge ([d698f12](https://github.com/gfargo/buoy/commit/d698f121918ea2d1f7c12c27ba7611729547405a))
* **packaging:** ship static/ assets in wheel via force-include ([#226](https://github.com/gfargo/buoy/issues/226)) ([a4c3ef7](https://github.com/gfargo/buoy/commit/a4c3ef7f14523fbccbc2d08dc44a19d1d18c02a7))
* **plugins:** enforce config_schema defaults, type coercion, and required fields ([#240](https://github.com/gfargo/buoy/issues/240)) ([1bb16d8](https://github.com/gfargo/buoy/commit/1bb16d8bdbc110f1368170acefb15fa65166b4bd))
* **plugins:** fix operator precedence bug in loadPluginJS ([2b7effd](https://github.com/gfargo/buoy/commit/2b7effd9eb176701ea083896086b1f811f832c03))
* **plugins:** fix operator precedence bug in loadPluginJS breaking custom renderers ([2b7effd](https://github.com/gfargo/buoy/commit/2b7effd9eb176701ea083896086b1f811f832c03))
* **plugins:** fix operator precedence bug in loadPluginJS breaking custom renderers ([814bf9a](https://github.com/gfargo/buoy/commit/814bf9a2b894f6d738ee155f800a3da2adc8f9bb))
* **plugins:** guard _find_plugin_class against imported/shared-base classes ([#225](https://github.com/gfargo/buoy/issues/225)) ([65b0563](https://github.com/gfargo/buoy/commit/65b05635f13a124bdbb6d56e48c8338553bb5ef2))
* **plugins:** honour refresh.plugins_interval as server-side collection floor ([#210](https://github.com/gfargo/buoy/issues/210)) ([d4bc8f5](https://github.com/gfargo/buoy/commit/d4bc8f5d855c373bc830372c50d36afe38de64bb))
* **plugins:** make speedtest-cli an optional dependency ([#151](https://github.com/gfargo/buoy/issues/151)) ([5c7e8e9](https://github.com/gfargo/buoy/commit/5c7e8e9fff26020c80bd97b4472dedaa294b70f1))
* **plugins:** move Plugin.config to per-instance __init__ ([#207](https://github.com/gfargo/buoy/issues/207)) ([2c53c57](https://github.com/gfargo/buoy/commit/2c53c571ed7e6cfddfd00e6374af4846b3f28705))
* **plugins:** report never-collected plugins as pending instead of omitting them ([#231](https://github.com/gfargo/buoy/issues/231)) ([3ab4d6a](https://github.com/gfargo/buoy/commit/3ab4d6af17fcf5ccd178d06e32bdce85ee5c6142))
* **prometheus:** escape label values and use exact uptime from /proc/uptime ([#209](https://github.com/gfargo/buoy/issues/209)) ([b1ac148](https://github.com/gfargo/buoy/commit/b1ac148e1f11d91329cb267ffd6645f57e9fce7d))
* **proxy:** add trusted-proxy header handling (BUG-46) ([#260](https://github.com/gfargo/buoy/issues/260)) ([badaff2](https://github.com/gfargo/buoy/commit/badaff2347388116308fab99bb93a6e68fa3a351))
* resolve CI lint failures ([bfe0573](https://github.com/gfargo/buoy/commit/bfe0573f8cbd1536b1249cb02f67db80230674e3))
* resolve static directory path for Docker installs ([f147b58](https://github.com/gfargo/buoy/commit/f147b58fc59308b92182ea4d276f626f922dd551))
* **security:** escape untrusted strings before innerHTML (SEC-3 stored XSS) ([#212](https://github.com/gfargo/buoy/issues/212)) ([f12b310](https://github.com/gfargo/buoy/commit/f12b310a7a6ec0b817fa48f0406e6fe1cb4c413e))
* **server:** add Content-Security-Policy header (SEC-6) ([#239](https://github.com/gfargo/buoy/issues/239)) ([6d61b80](https://github.com/gfargo/buoy/commit/6d61b800590bbb50eaed3bd8d9f79e82563e7958))
* **server:** add shutdown path via lifespan (plugin teardown + SQLite close) ([#266](https://github.com/gfargo/buoy/issues/266)) ([f68bf78](https://github.com/gfargo/buoy/commit/f68bf78d82128ee070201ec6dc957178229b3f8d))
* **server:** gate /api/config/debug independently of auth.enabled ([#208](https://github.com/gfargo/buoy/issues/208)) ([d67ca12](https://github.com/gfargo/buoy/commit/d67ca1281701e6b7a58c3214b26d44ff241e4731))
* **server:** same-origin CORS by default, opt-in allowlist for fleet ([#211](https://github.com/gfargo/buoy/issues/211)) ([8926add](https://github.com/gfargo/buoy/commit/8926add956548be59f47c2cde2b8515ab2daa78f))
* **services:** match hidden patterns against compose-style container names ([#219](https://github.com/gfargo/buoy/issues/219)) ([7d5d84c](https://github.com/gfargo/buoy/commit/7d5d84c7c8ec3218a3aafa6c40fb4dee5684df7a))
* **storage:** move blocking SQLite I/O off the event loop, fix prune cadence ([#264](https://github.com/gfargo/buoy/issues/264)) ([8cbce30](https://github.com/gfargo/buoy/commit/8cbce3007c29fa97b3c78e1146f7ffc3cf3dc3c9))
* **subprocess:** kill and reap timed-out child processes ([#263](https://github.com/gfargo/buoy/issues/263)) ([284fbac](https://github.com/gfargo/buoy/commit/284fbac40004bacb37ef9a830d775146c267b366))
* **tests:** resolve ruff lint errors (N806, I001, F401, F841) ([89a3b4f](https://github.com/gfargo/buoy/commit/89a3b4fcb745ee0b0e36ea78f99a09220f4d5418))
* **tests:** resolve ruff lint errors in test files ([fcebaf1](https://github.com/gfargo/buoy/commit/fcebaf18b1da8ea8f5725bb298217a5118108a59))
* **theme:** apply theme.custom CSS variable overrides at page load ([#150](https://github.com/gfargo/buoy/issues/150)) ([70914c8](https://github.com/gfargo/buoy/commit/70914c8fae3fdb1e6dd02fa785ee5b0447ea2204))


### Documentation

* add community-health and repo-hygiene files ([#236](https://github.com/gfargo/buoy/issues/236)) ([98cd43d](https://github.com/gfargo/buoy/commit/98cd43d8c8c75f951ff5e9c12595add732220545))
* add design spec (reference for open issues [#1](https://github.com/gfargo/buoy/issues/1)–[#11](https://github.com/gfargo/buoy/issues/11)) ([1d1a14c](https://github.com/gfargo/buoy/commit/1d1a14c182ae5b887c5ea740d281fa045333e228))
* **changelog:** reorder newest-first, add 2.0.3, 2.1.0, and populated Unreleased ([#221](https://github.com/gfargo/buoy/issues/221)) ([45ee174](https://github.com/gfargo/buoy/commit/45ee17489c99684dae6b1ffbfe44d74006d7fc56))
* **deployment:** document native, Kubernetes, and Ansible deployment paths ([#253](https://github.com/gfargo/buoy/issues/253)) ([40ba676](https://github.com/gfargo/buoy/commit/40ba676298fc9041bd9ce9df1a4d82f8dce85c7a))
* move documentation to wiki, remove roadmap from README ([8b52cef](https://github.com/gfargo/buoy/commit/8b52cef9bcc92ab78141b57d70975f850658cde5))
* **plugins:** document all 22 built-in plugins, add CI coverage check ([#215](https://github.com/gfargo/buoy/issues/215)) ([b1bc595](https://github.com/gfargo/buoy/commit/b1bc59598d510ce5140bbb81c21eb0626640efb8))
* **spec:** mark SPEC.md as historical, correct stale claims ([#234](https://github.com/gfargo/buoy/issues/234)) ([566aef7](https://github.com/gfargo/buoy/commit/566aef749ba00c303742efac57bdd4d5a77a61f4))

## [2.1.0] - 2026-06-27

### Added
- Container detail panel with metadata, logs, and restart action (#1)
- Deploy info footer showing version, build date, and git SHA (#8)
- Tailscale ping for peer latency measurement with HTTP fallback (#15)
- Fleet node cards now show service link pills (#16)

## [2.0.3] - 2026-06-27

### Fixed
- Operator precedence bug in `loadPluginJS` breaking custom plugin renderers (fixed in two passes)

## [2.0.2] - 2026-06-26

### Fixed
- NVMe SMART collection now uses `nsenter -t 1 -m` to access host smartctl from within containers (#2, PR #12)
- Removed `os.path.exists('/dev/nvme0n1')` guard that always blocked NVMe collection in Docker
- Health badge in gauges.js reflects actual wear level (Healthy / Warning ≥70% / Critical ≥90%)
- Added `.health-badge.crit` CSS style for critical NVMe state
- Static directory path resolution for Docker installs (`/app/static` fallback)

### Changed
- Design spec added as reference document for open issues #1–#11

## [2.0.1] - 2026-06-25

### Fixed
- Dockerfile build order: copy `src/` before `pip install` (hatchling needs `__init__.py` for version)
- Include `README.md` in Docker build context for hatchling metadata
- Resolved CI lint failures (E402, long lines, ruff format)

### Changed
- CI: build only linux/amd64 in CI for speed; multi-arch reserved for releases
- Release workflow: trigger on `v*` tags with semver Docker tags
- Documentation moved to GitHub wiki; roadmap removed from README
- Ruff config: ignore E501 (inline JS) and F823 (module globals)

## [2.0.0] - 2026-06-25

Initial public release. Complete rewrite from the internal "hub" dashboard.

### Added

**Core:**
- Starlette async server with WebSocket support
- Single `buoy.yaml` config file with environment variable overlay
- Multi-arch Docker image (amd64 + arm64)
- Demo mode (`--demo`) for zero-infrastructure evaluation

**Collectors:**
- System: CPU, memory, temperature, uptime, device model
- Docker: container discovery, stats, inspect, logs, restart
- Disk: mount info (with nsenter for containers), NVMe SMART data
- Network: fleet peer polling via httpx

**Frontend:**
- Modular vanilla JS (ES modules, no build step)
- Terminal dark theme + light theme (CSS custom properties)
- Expandable detail panels (CPU breakdown, memory, disk mounts, containers)
- Sparklines for temperature and disk trends
- Night mode (auto/always/never)
- Keyboard shortcuts (1-4 for panels, Escape to close)
- Responsive layout (desktop, tablet, mobile)
- Accessibility: semantic HTML, ARIA labels, keyboard navigation

**Plugin System:**
- Python plugin protocol (base class, manifest, PanelData)
- Plugin loader with auto-discovery (builtin + user directory)
- Per-plugin refresh intervals with error isolation
- Custom frontend JS injection for rich plugin UIs
- 7 built-in plugins:
  - GitHub (notifications + open PRs)
  - UptimeKuma (service health badges)
  - Loki (recent error logs)
  - Plane (sprint/cycle progress)
  - Backup Status (backup freshness + health)
  - Cron Health (recent cron job runs)
  - Prometheus Exporter (`/metrics` endpoint)

**Security:**
- Optional token/basic auth for destructive endpoints
- Rate limiting (60 req/min per IP on protected paths)
- Container name validation (prevents injection)
- Security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy)

**History + Alerts:**
- SQLite ring buffer (24h retention, auto-prune, WAL mode)
- History API (`/api/history/{metric}?period=1h|6h|24h`)
- Alert engine with duration-aware threshold detection
- WebSocket push notifications (toast UI)
- Optional webhook dispatch (Discord/Slack/generic)

**Service Discovery:**
- Auto-discovers running Docker containers
- Tailscale-aware URL generation (HTTPS when accessed via .ts.net)
- Configurable hidden list + display overrides

**Documentation:**
- Full configuration reference
- Plugin development guide
- Deployment patterns (single node, fleet, reverse proxy)
- Contributing guide
