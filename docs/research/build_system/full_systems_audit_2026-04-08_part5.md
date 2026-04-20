# InterGenOS Full Systems Audit — Part 5 of 5
# Complete Dependency Graph (541 Packages)

**Date:** 2026-04-08
**Prepared for:** External Security Auditors

---

## Full Package Dependency Graph

Generated from the igos-build dependency resolver using Kahn's topological sort.
Each entry shows: build position, tier, package name, version, and resolved dependencies.

Format: `N. [tier] name version | deps: dep1, dep2, ...`

- "none" means the package has no declared dependencies
- Dependencies listed are the union of build, host, and runtime dependencies
- Packages are ordered so every dependency appears before its dependents

Total packages: 541

```
1. [toolchain] linux-headers 6.18.10 | deps: none
2. [toolchain] binutils-pass1 2.46.0 | deps: none
3. [core] attr 2.5.2 | deps: none
4. [core] bc 7.0.3 | deps: none
5. [core] bison-core 3.8.2 | deps: none
6. [core] bzip2 1.0.8 | deps: none
7. [core] diffutils-core 3.12 | deps: none
8. [core] expat 2.7.4 | deps: none
9. [core] file 5.46 | deps: none
10. [core] findutils-core 4.10.0 | deps: none
11. [core] flex 2.6.4 | deps: none
12. [core] glibc-core 2.43 | deps: none
13. [core] gmp 6.3.0 | deps: none
14. [core] gperf 3.3 | deps: none
15. [core] grep-core 3.12 | deps: none
16. [core] groff 1.23.0 | deps: none
17. [core] grub 2.14 | deps: none
18. [core] gzip-core 1.14 | deps: none
19. [core] iana-etc 20260202 | deps: none
20. [core] libarchive 3.8.6 | deps: none
21. [core] libffi 3.5.2 | deps: none
22. [core] libpipeline 1.5.8 | deps: none
23. [core] libtasn1 4.21.0 | deps: none
24. [core] libtool 2.5.4 | deps: none
25. [core] libunistring 1.4.2 | deps: none
26. [core] libxcrypt 4.5.2 | deps: none
27. [core] lz4 1.10.0 | deps: none
28. [core] m4-core 1.4.21 | deps: none
29. [core] make-core 4.4.1 | deps: none
30. [core] man-pages 6.17 | deps: none
31. [core] ncurses-core 6.6 | deps: none
32. [core] nghttp2 1.68.1 | deps: none
33. [core] nspr 4.38.2 | deps: none
34. [core] patch-core 2.8 | deps: none
35. [core] pkgconf 2.5.1 | deps: none
36. [core] sed-core 4.9 | deps: none
37. [core] xz 5.8.2 | deps: none
38. [core] zlib 1.3.2 | deps: none
39. [base] cpio 2.15 | deps: none
40. [base] fcron 3.4.0 | deps: none
41. [base] libtirpc 1.3.7 | deps: none
42. [base] pax 20240817 | deps: none
43. [base] perl-file-fcntllock 0.22 | deps: none
44. [base] popt 1.19 | deps: none
45. [base] strace 6.19 | deps: none
46. [base] time 1.9 | deps: none
47. [base] which 2.23 | deps: none
48. [desktop] Mako 1.3.10 | deps: none
49. [desktop] alsa-lib 1.2.15.3 | deps: none
50. [desktop] bash-completion 2.17.0 | deps: none
51. [desktop] boost 1.90.0 | deps: none
52. [desktop] bubblewrap 0.11.0 | deps: none
53. [desktop] cdparanoia 10.2 | deps: none
54. [desktop] cracklib 2.10.3 | deps: none
55. [desktop] cython 3.2.4 | deps: none
56. [desktop] docutils 0.22.4 | deps: none
57. [desktop] dosfstools 4.2 | deps: none
58. [desktop] duktape 2.7.0 | deps: none
59. [desktop] editables 0.5 | deps: none
60. [desktop] fdk-aac 2.0.3 | deps: none
61. [desktop] font-alias 1.0.6 | deps: none
62. [desktop] font-dejavu 2.37 | deps: none
63. [desktop] font-noto 2025.12.01 | deps: none
64. [desktop] font-util 1.4.1 | deps: none
65. [desktop] fribidi 1.0.16 | deps: none
66. [desktop] fuse3 3.18.1 | deps: none
67. [desktop] giflib 5.2.2 | deps: none
68. [desktop] gnome-backgrounds 49.0 | deps: none
69. [desktop] help2man 1.49.3 | deps: none
70. [desktop] hicolor-icon-theme 0.18 | deps: none
71. [desktop] hwdata 0.404 | deps: none
72. [desktop] icu 78.2 | deps: none
73. [desktop] inih 62 | deps: none
74. [desktop] iptables 1.8.12 | deps: none
75. [desktop] iso-codes 4.20.1 | deps: none
76. [desktop] jansson 2.15.0 | deps: none
77. [desktop] keyutils 1.6.3 | deps: none
78. [desktop] lame 3.100 | deps: none
79. [desktop] lcms2 2.18 | deps: none
80. [desktop] libaio 0.3.113 | deps: none
81. [desktop] libatasmart 0.19 | deps: none
82. [desktop] libcdio 2.1.0 | deps: none
83. [desktop] libdaemon 0.14 | deps: none
84. [desktop] libevdev 1.13.6 | deps: none
85. [desktop] libevent 2.1.12 | deps: none
86. [desktop] libexif 0.6.25 | deps: none
87. [desktop] libfyaml 0.9.4 | deps: none
88. [desktop] libgpg-error 1.59 | deps: none
89. [desktop] libmnl 1.0.5 | deps: none
90. [desktop] libndp 1.9 | deps: none
91. [desktop] libnfs 6.0.2 | deps: none
92. [desktop] libnl 3.12.0 | deps: none
93. [desktop] libogg 1.3.6 | deps: none
94. [desktop] libpcap 1.10.6 | deps: none
95. [desktop] libplist 2.7.0 | deps: none
96. [desktop] libpng 1.6.55 | deps: none
97. [desktop] libsamplerate 0.2.2 | deps: none
98. [desktop] libseccomp 2.6.0 | deps: none
99. [desktop] libusb 1.0.29 | deps: none
100. [desktop] libxcvt 0.1.3 | deps: none
101. [desktop] libyaml 0.2.5 | deps: none
102. [desktop] lmdb 0.9.35 | deps: none
103. [desktop] lua 5.4.8 | deps: none
104. [desktop] mandoc 1.14.6 | deps: none
105. [desktop] markdown 3.10.2 | deps: none
106. [desktop] mtdev 1.1.7 | deps: none
107. [desktop] nasm 3.01 | deps: none
108. [desktop] nettle 3.10.2 | deps: none
109. [desktop] npth 1.8 | deps: none
110. [desktop] opus 1.6.1 | deps: none
111. [desktop] orc 0.4.42 | deps: none
112. [desktop] pathspec 1.0.4 | deps: none
113. [desktop] perl-parse-yapp 1.21 | deps: none
114. [desktop] pixman 0.46.4 | deps: none
115. [desktop] rpcsvc-proto 1.4.4 | deps: none
116. [desktop] sassc 3.6.2 | deps: none
117. [desktop] setuptools-scm 9.2.2 | deps: none
118. [desktop] sgml-common 0.6.3 | deps: none
119. [desktop] sound-theme-freedesktop 0.8 | deps: none
120. [desktop] soundtouch 2.4.0 | deps: none
121. [desktop] trove-classifiers 2026.1.14.14 | deps: none
122. [desktop] unifdef 2.12 | deps: none
123. [desktop] util-macros 1.20.2 | deps: none
124. [desktop] wireless-regdb 2026.02.04 | deps: none
125. [desktop] xcb-proto 1.17.0 | deps: none
126. [desktop] xtrans 1.6.0 | deps: none
127. [toolchain] gcc-pass1 15.2.0 | deps: binutils-pass1
128. [core] acl 2.3.2 | deps: attr
129. [core] mpfr 4.2.2 | deps: gmp
130. [base] ed 1.22.5 | deps: libarchive
131. [core] libidn2 2.3.8 | deps: libunistring
132. [core] shadow 4.19.3 | deps: libxcrypt
133. [core] zstd 1.5.7 | deps: lz4
134. [core] gettext 1.0 | deps: ncurses-core
135. [core] less 692 | deps: ncurses-core
136. [core] psmisc 23.7 | deps: ncurses-core
137. [core] readline 8.3 | deps: ncurses-core
138. [core] vim 9.2.0078 | deps: ncurses-core
139. [core] tcl 8.6.17 | deps: zlib
140. [base] libnsl 2.0.1 | deps: libtirpc
141. [base] lsof 4.99.6 | deps: libtirpc
142. [base] rsync 3.4.1 | deps: popt
143. [desktop] aspell 0.60.8.2 | deps: which
144. [desktop] alsa-plugins 1.2.12 | deps: alsa-lib
145. [desktop] alsa-utils 1.2.15.2 | deps: alsa-lib
146. [desktop] mpg123 1.33.4 | deps: alsa-lib
147. [desktop] pipewire 1.6.0 | deps: alsa-lib
148. [desktop] libdisplay-info 0.3.0 | deps: hwdata
149. [desktop] libcdio-paranoia 10.2+2.0.2 | deps: libcdio
150. [desktop] links 2.30 | deps: libevent
151. [desktop] libassuan 3.0.2 | deps: libgpg-error
152. [desktop] libgcrypt 1.12.0 | deps: libgpg-error
153. [desktop] libksba 1.6.7 | deps: libgpg-error
154. [desktop] wpa_supplicant 2.11 | deps: libnl
155. [desktop] flac 1.5.0 | deps: libogg
156. [desktop] libvorbis 1.3.7 | deps: libogg
157. [desktop] speex 1.2.1 | deps: libogg
158. [desktop] libimobiledevice-glue 1.3.2 | deps: libplist
159. [desktop] libmtp 1.1.23 | deps: libusb
160. [desktop] pyyaml-pass2 6.0.3 | deps: cython, libyaml
161. [desktop] ruby 4.0.1 | deps: libyaml
162. [desktop] cyrus-sasl 2.1.28 | deps: lmdb
163. [desktop] efivar 39 | deps: mandoc
164. [desktop] dav1d 1.5.3 | deps: nasm
165. [desktop] libaom 3.13.1 | deps: nasm
166. [desktop] libjpeg-turbo 3.1.3 | deps: nasm
167. [desktop] libvpx 1.16.0 | deps: nasm
168. [desktop] svt-av1 4.0.1 | deps: nasm
169. [desktop] x264 20250815 | deps: nasm
170. [desktop] x265 4.1 | deps: nasm
171. [desktop] rdfind 1.8.0 | deps: nettle
172. [desktop] pluggy 1.6.0 | deps: setuptools-scm
173. [desktop] libpciaccess 0.18.1 | deps: util-macros
174. [desktop] xbitmaps 1.1.3 | deps: util-macros
175. [desktop] xorgproto 2025.1 | deps: util-macros
176. [toolchain] glibc 2.43 | deps: binutils-pass1, gcc-pass1, linux-headers
177. [core] libcap 2.77 | deps: acl
178. [core] tar-core 1.35 | deps: acl, xz
179. [core] mpc 1.3.1 | deps: mpfr
180. [core] elfutils 0.194 | deps: bzip2, xz, zlib, zstd
181. [core] gawk-core 5.3.2 | deps: readline
182. [core] gdbm 1.26 | deps: readline
183. [core] inetutils 2.7 | deps: ncurses-core, readline
184. [core] pcre2 10.47 | deps: readline, zlib
185. [core] sqlite 3510200 | deps: readline
186. [core] util-linux-core 2.41.3 | deps: ncurses-core, readline, zlib
187. [desktop] slang 2.3.3 | deps: readline
188. [core] expect 5.45.4 | deps: tcl
189. [desktop] pinentry 1.3.2 | deps: libassuan, libgpg-error
190. [desktop] libsndfile 1.2.2 | deps: flac, libvorbis, opus
191. [desktop] libusbmuxd 2.1.1 | deps: libimobiledevice-glue, libplist
192. [desktop] efibootmgr 18 | deps: efivar
193. [desktop] libtiff 4.7.1 | deps: libjpeg-turbo
194. [desktop] qpdf 12.3.2 | deps: libjpeg-turbo
195. [desktop] libavif 1.3.0 | deps: dav1d, svt-av1
196. [desktop] libheif 1.21.2 | deps: dav1d, libaom, x265
197. [desktop] hatchling 1.28.0 | deps: editables, pathspec, pluggy, trove-classifiers
198. [desktop] libdrm 2.4.131 | deps: libpciaccess
199. [desktop] bdftopcf 1.1 | deps: xorgproto
200. [desktop] libFS 1.0.10 | deps: xorgproto, xtrans
201. [desktop] libICE 1.1.2 | deps: xorgproto, xtrans
202. [desktop] libXau 1.0.12 | deps: xorgproto
203. [desktop] libXdmcp 1.1.5 | deps: xorgproto
204. [desktop] libfontenc 1.1.9 | deps: xorgproto
205. [desktop] libxshmfence 1.3.3 | deps: xorgproto
206. [desktop] sessreg 1.1.4 | deps: xorgproto
207. [toolchain] libstdcpp 15.2.0 | deps: glibc
208. [core] gcc-core 15.2.0 | deps: gmp, mpc, mpfr, zlib, zstd
209. [core] binutils-core 2.46.0 | deps: elfutils, zlib, zstd
210. [core] iproute2 6.18.0 | deps: elfutils
211. [core] man-db 2.13.1 | deps: gdbm, groff, libpipeline
212. [core] perl-core 5.42.0 | deps: gdbm, libxcrypt
213. [core] e2fsprogs 1.47.3 | deps: util-linux-core
214. [desktop] newt 0.52.25 | deps: popt, slang
215. [core] dejagnu 1.6.3 | deps: expect
216. [desktop] pulseaudio 17.0 | deps: alsa-lib, libsndfile, speex
217. [desktop] sbc 2.2 | deps: libsndfile
218. [desktop] libwebp 1.6.0 | deps: libjpeg-turbo, libpng, libtiff
219. [desktop] argcomplete 3.6.3 | deps: hatchling
220. [desktop] hatch-fancy-pypi-readme 25.1.0 | deps: hatchling
221. [desktop] hatch-vcs 0.5.0 | deps: hatchling, setuptools-scm
222. [desktop] pygments 2.19.2 | deps: hatchling
223. [desktop] iceauth 1.0.10 | deps: libICE
224. [desktop] libSM 1.2.6 | deps: libICE
225. [desktop] libxcb 1.17.0 | deps: libXau, libXdmcp, xcb-proto
226. [toolchain] m4 1.4.21 | deps: libstdcpp
227. [core] autoconf 2.72 | deps: perl-core
228. [core] openssl 3.6.1 | deps: perl-core
229. [core] texinfo-core 7.2 | deps: perl-core
230. [core] xml-parser 2.47 | deps: expat, perl-core
231. [desktop] parallel 20260322 | deps: perl-core
232. [desktop] mitkrb 1.22.2 | deps: e2fsprogs, keyutils
233. [desktop] attrs 25.4.0 | deps: hatch-fancy-pypi-readme, hatch-vcs
234. [desktop] libbytesize 2.12 | deps: pygments
235. [desktop] xcb-util 0.4.1 | deps: libxcb
236. [desktop] xcb-util-wm 0.4.2 | deps: libxcb
237. [toolchain] ncurses 6.6 | deps: m4
238. [core] automake 1.18.1 | deps: autoconf
239. [core] kbd 2.9.0 | deps: autoconf, pkgconf
240. [core] libssh2 1.11.1 | deps: openssl
241. [core] linux-kernel 6.18.10 | deps: bc, elfutils, openssl
242. [core] python 3.14.3 | deps: expat, libffi, ncurses-core, openssl, readline, sqlite
243. [desktop] openldap 2.6.12 | deps: cyrus-sasl, openssl
244. [core] intltool 0.51.0 | deps: perl-core, xml-parser
245. [desktop] linux-firmware 20260309 | deps: parallel, rdfind
246. [desktop] libei 1.5.0 | deps: attrs
247. [desktop] xcb-util-image 0.4.1 | deps: xcb-util
248. [desktop] xcb-util-keysyms 0.4.1 | deps: libxcb, xcb-util
249. [desktop] xcb-util-renderutil 0.3.10 | deps: libxcb, xcb-util
250. [toolchain] bash-tmp 5.3 | deps: ncurses
251. [toolchain] coreutils-tmp 9.10 | deps: ncurses
252. [toolchain] diffutils-tmp 3.12 | deps: ncurses
253. [toolchain] file-tmp 5.46 | deps: ncurses
254. [toolchain] findutils-tmp 4.10.0 | deps: ncurses
255. [toolchain] gawk-tmp 5.3.2 | deps: ncurses
256. [toolchain] gettext-tmp 1.0 | deps: ncurses
257. [toolchain] grep-tmp 3.12 | deps: ncurses
258. [toolchain] gzip-tmp 1.14 | deps: ncurses
259. [toolchain] make-tmp 4.4.1 | deps: ncurses
260. [toolchain] patch-tmp 2.8 | deps: ncurses
261. [toolchain] sed-tmp 4.9 | deps: ncurses
262. [toolchain] tar-tmp 1.35 | deps: ncurses
263. [toolchain] xz-tmp 5.8.2 | deps: ncurses
264. [core] bash 5.3 | deps: bison-core, ncurses, ncurses-core, readline
265. [core] nano 8.7.1 | deps: gettext, ncurses
266. [base] htop 3.4.1 | deps: ncurses
267. [base] iotop 1.31 | deps: ncurses
268. [desktop] lynx 2.9.2 | deps: ncurses, openssl, zlib
269. [core] coreutils-core 9.10 | deps: acl, autoconf, automake, libcap
270. [core] libuv 1.52.1 | deps: autoconf, automake, libtool
271. [core] flit-core 3.12.0 | deps: python
272. [core] ninja 1.13.2 | deps: python
273. [desktop] linux-kernel-pass2 6.18.10 | deps: linux-firmware
274. [desktop] xcb-util-cursor 0.1.6 | deps: xcb-util-image, xcb-util-renderutil
275. [toolchain] bison-tmp 3.8.2 | deps: gettext-tmp
276. [toolchain] binutils-pass2 2.46.0 | deps: xz-tmp
277. [core] packaging 26.0 | deps: flit-core
278. [core] wheel 0.46.3 | deps: flit-core
279. [core] meson 1.10.1 | deps: ninja, python
280. [toolchain] perl-tmp 5.42.0 | deps: bison-tmp
281. [toolchain] gcc-pass2 15.2.0 | deps: binutils-pass2
282. [core] setuptools 82.0.0 | deps: wheel
283. [core] glib2-bootstrap 2.86.4 | deps: meson, ninja
284. [core] kmod 34.2 | deps: meson, ninja, openssl, xz, zlib, zstd
285. [core] libpsl 0.21.5 | deps: libidn2, libunistring, meson, ninja
286. [core] linux-pam 1.7.2 | deps: meson, ninja
287. [core] p11-kit 0.26.2 | deps: libtasn1, meson, ninja
288. [toolchain] python-tmp 3.14.3 | deps: perl-tmp
289. [core] markupsafe 3.0.3 | deps: setuptools
290. [core] pyyaml 6.0.3 | deps: setuptools
291. [core] gobject-introspection 1.86.0 | deps: glib2-bootstrap, meson, ninja
292. [core] openssh 10.2p1 | deps: linux-pam, openssl
293. [core] shadow-pam 4.19.3 | deps: linux-pam
294. [core] sudo 1.9.17p2 | deps: linux-pam
295. [base] exim 4.99.1 | deps: libnsl, linux-pam, perl-file-fcntllock
296. [base] screen 5.0.1 | deps: linux-pam
297. [desktop] libpwquality 1.4.5 | deps: cracklib, linux-pam
298. [core] make-ca 1.16.1 | deps: p11-kit
299. [core] nss 3.121 | deps: nspr, p11-kit
300. [toolchain] texinfo-tmp 7.2 | deps: python-tmp
301. [core] jinja2 3.1.6 | deps: markupsafe
302. [core] glib2 2.86.4 | deps: gobject-introspection, meson, ninja
303. [base] at 3.2.5 | deps: exim, linux-pam
304. [core] curl 8.19.0 | deps: libpsl, libssh2, make-ca, openssl
305. [core] wget 1.25.0 | deps: libpsl, make-ca, openssl
306. [desktop] gnutls 3.8.12 | deps: libtasn1, libunistring, make-ca, nettle, p11-kit
307. [toolchain] util-linux-tmp 2.41.3 | deps: texinfo-tmp
308. [core] systemd 259.1 | deps: acl, elfutils, expat, jinja2, kmod, libcap, meson, ninja, openssl, util-linux-core
309. [base] atop 2.12.1 | deps: glib2
310. [desktop] desktop-file-utils 0.28 | deps: glib2
311. [desktop] gnome-menus 3.38.1 | deps: glib2
312. [desktop] graphene 1.10.8 | deps: glib2
313. [desktop] gsettings-desktop-schemas 49.1 | deps: glib2
314. [desktop] gstreamer 1.28.1 | deps: glib2
315. [desktop] json-glib 1.10.8 | deps: glib2
316. [desktop] libgtop 2.41.3 | deps: glib2, libXau
317. [desktop] libgudev 238 | deps: glib2
318. [desktop] libmbim 1.34.0 | deps: bash-completion, glib2, help2man
319. [desktop] libxmlb 0.3.25 | deps: glib2
320. [desktop] wireplumber 0.5.13 | deps: glib2, lua, pipewire
321. [desktop] xdg-dbus-proxy 0.1.6 | deps: glib2
322. [core] cmake 4.3.1 | deps: curl, libarchive, libuv, nghttp2
323. [core] git 2.53.0 | deps: curl, pcre2
324. [desktop] libgphoto2 2.5.33 | deps: curl, libexif, libusb
325. [desktop] libtatsu 1.0.5 | deps: curl, libplist
326. [desktop] gnupg2 2.5.17 | deps: gnutls, libassuan, libgcrypt, libksba, npth, pinentry
327. [core] dbus 1.16.2 | deps: expat, meson, ninja, systemd
328. [core] procps-ng 4.0.6 | deps: ncurses-core, systemd
329. [desktop] lvm2 2.03.38 | deps: libaio, systemd
330. [desktop] glib-networking 2.80.1 | deps: gnutls, gsettings-desktop-schemas
331. [desktop] upower 1.91.1 | deps: libgudev, libusb
332. [desktop] libqmi 1.38.0 | deps: glib2, libgudev, libmbim
333. [base] btop 1.4.6 | deps: cmake
334. [desktop] abseil-cpp 20260107.1 | deps: cmake
335. [desktop] brotli 1.2.0 | deps: cmake
336. [desktop] c-ares 1.34.6 | deps: cmake
337. [desktop] graphite2 1.3.14 | deps: cmake
338. [desktop] highway 1.3.0 | deps: cmake
339. [desktop] json-c 0.18 | deps: cmake
340. [desktop] llvm 21.1.8 | deps: cmake
341. [desktop] openjpeg2 2.5.4 | deps: cmake
342. [desktop] spirv-headers 1.4.341.0 | deps: cmake
343. [desktop] vulkan-headers 1.4.341.0 | deps: cmake
344. [desktop] doxygen 1.16.1 | deps: cmake, git
345. [desktop] libimobiledevice 1.4.0 | deps: libimobiledevice-glue, libplist, libtatsu, libusb, libusbmuxd, openssl
346. [desktop] gpgme 2.0.1 | deps: gnupg2, libassuan
347. [desktop] cups 2.4.16 | deps: dbus, gnutls, libusb, linux-pam
348. [desktop] protobuf 33.5 | deps: abseil-cpp
349. [desktop] exiv2 0.28.7 | deps: brotli, curl, inih
350. [desktop] freetype2-pass1 2.14.1 | deps: brotli, libpng
351. [desktop] woff2 1.0.2 | deps: brotli
352. [extra] nodejs 22.22.0 | deps: brotli, c-ares, icu, libuv, nghttp2, which
353. [desktop] libjxl 0.11.2 | deps: brotli, giflib, highway, lcms2, libjpeg-turbo, libpng
354. [desktop] cryptsetup 2.8.4 | deps: json-c, lvm2, popt
355. [desktop] libnvme 1.16.1 | deps: json-c, keyutils
356. [desktop] rust 1.93.1 | deps: cmake, curl, llvm
357. [desktop] spirv-tools 1.4.341.0 | deps: spirv-headers
358. [desktop] libxml2 2.15.1 | deps: doxygen, icu, readline
359. [desktop] samba 4.23.5 | deps: fuse3, gnutls, gpgme, jansson, libtirpc, linux-pam, mitkrb, openldap, perl-parse-yapp, rpcsvc-proto
360. [desktop] protobuf-c 1.5.2 | deps: protobuf
361. [desktop] harfbuzz 12.3.2 | deps: freetype2-pass1, graphite2, icu
362. [extra] chrome-helper 1.0 | deps: nodejs
363. [extra] claude-code-helper 1.0 | deps: nodejs
364. [extra] vscode-helper 1.0 | deps: nodejs
365. [desktop] libblockdev 3.4.0 | deps: cryptsetup, keyutils, libatasmart, libbytesize, libnvme, lvm2
366. [desktop] cargo-c 0.10.20 | deps: libssh2, rust
367. [desktop] cbindgen 0.29.2 | deps: rust
368. [desktop] rust-bindgen 0.72.1 | deps: rust
369. [desktop] glslang 16.2.0 | deps: spirv-headers, spirv-tools
370. [desktop] docbook-xml 4.5 | deps: libarchive, libxml2
371. [desktop] libwacom 2.18.0 | deps: libevdev, libgudev, libxml2
372. [desktop] libxslt 1.1.45 | deps: libxml2
373. [desktop] shared-mime-info 2.4 | deps: glib2, libxml2
374. [desktop] spirv-llvm-translator 21.1.4 | deps: libxml2, llvm, spirv-tools
375. [desktop] totem-pl-parser 3.26.6 | deps: libarchive, libgcrypt, libxml2
376. [desktop] wayland 1.24.0 | deps: libxml2
377. [desktop] freetype2 2.14.1 | deps: brotli, harfbuzz, libpng
378. [desktop] spidermonkey 140.8.0 | deps: cbindgen, icu, llvm, readline, rust, zlib
379. [desktop] shaderc 2026.1 | deps: cmake, glslang, spirv-tools
380. [desktop] docbook-xsl-nons 1.79.2 | deps: docbook-xml, libxml2
381. [desktop] libinput 1.31.0 | deps: libevdev, libwacom, mtdev
382. [desktop] lxml 6.0.2 | deps: libxslt
383. [desktop] polkit 127 | deps: duktape, glib2, libxslt, linux-pam
384. [desktop] xdg-user-dirs 0.19 | deps: libxslt
385. [desktop] gdk-pixbuf 2.44.5 | deps: giflib, libjpeg-turbo, libpng, libtiff, shared-mime-info
386. [desktop] libclc 21.1.8 | deps: llvm, spirv-llvm-translator
387. [desktop] wayland-protocols 1.47 | deps: wayland
388. [desktop] fontconfig 2.17.1 | deps: freetype2
389. [desktop] libXfont2 2.0.7 | deps: freetype2, libfontenc, xorgproto, xtrans
390. [desktop] mupdf 1.26.12 | deps: freetype2, harfbuzz, libjpeg-turbo, openjpeg2
391. [desktop] xmlto 0.0.29 | deps: docbook-xml, docbook-xsl-nons, libxslt
392. [desktop] itstool 2.0.7 | deps: docbook-xml, lxml
393. [desktop] rtkit 0.13 | deps: dbus, libcap, polkit, systemd
394. [desktop] systemd-pass2 259.1 | deps: linux-pam, polkit
395. [desktop] udisks2 2.11.1 | deps: libatasmart, libblockdev, libgudev, lvm2, polkit
396. [desktop] ghostscript 10.06.0 | deps: cups, fontconfig, freetype2, lcms2, libjpeg-turbo, libpng, libtiff, openjpeg2
397. [desktop] libX11 1.8.13 | deps: fontconfig, libxcb, xorgproto, xtrans
398. [desktop] libass 0.17.4 | deps: fontconfig, freetype2, fribidi, nasm
399. [desktop] libbluray 1.4.1 | deps: fontconfig, libxml2
400. [desktop] xdg-utils 1.2.1 | deps: lynx, xmlto
401. [desktop] appstream 1.1.2 | deps: curl, itstool, libfyaml, libxml2, libxmlb, libxslt
402. [desktop] gnome-user-docs 49.4 | deps: itstool, libxml2
403. [desktop] yelp-xsl 49.0 | deps: itstool, libxslt
404. [desktop] libXext 1.3.7 | deps: libX11
405. [desktop] libXfixes 6.0.2 | deps: libX11
406. [desktop] libXrender 0.9.12 | deps: libX11
407. [desktop] libXt 1.3.1 | deps: libICE, libSM, libX11
408. [desktop] libxkbfile 1.2.0 | deps: libX11
409. [desktop] mkfontscale 1.2.3 | deps: freetype2, libX11, libfontenc
410. [desktop] startup-notification 0.12 | deps: libX11, xcb-util
411. [desktop] xkeyboard-config 2.46 | deps: libX11
412. [desktop] xmodmap 1.0.11 | deps: libX11
413. [desktop] xprop 1.2.8 | deps: libX11
414. [desktop] xwininfo 1.1.6 | deps: libX11, libxcb
415. [desktop] ffmpeg 8.0.1 | deps: alsa-lib, dav1d, fdk-aac, freetype2, lame, libaom, libass, libvorbis, libvpx, nasm, openssl, opus, svt-av1, x264, x265
416. [desktop] libXScrnSaver 1.2.5 | deps: libXext
417. [desktop] libXinerama 1.1.6 | deps: libXext
418. [desktop] libXv 1.0.13 | deps: libXext
419. [desktop] libXxf86dga 1.1.7 | deps: libXext
420. [desktop] libXxf86vm 1.1.7 | deps: libXext
421. [desktop] libdmx 1.1.5 | deps: libXext
422. [desktop] libXcomposite 0.4.7 | deps: libXfixes
423. [desktop] libXdamage 1.1.7 | deps: libXfixes
424. [desktop] libXi 1.8.2 | deps: libXext, libXfixes
425. [desktop] cairo 1.18.4 | deps: fontconfig, freetype2, libX11, libXext, libXrender, libpng, pixman
426. [desktop] libXcursor 1.2.3 | deps: libXfixes, libXrender
427. [desktop] libXft 2.3.9 | deps: libXrender
428. [desktop] libXrandr 1.5.5 | deps: libXext, libXrender
429. [desktop] libXmu 1.3.1 | deps: libXext, libXt
430. [desktop] libXpm 3.5.18 | deps: libXext, libXt
431. [desktop] smproxy 1.0.8 | deps: libSM, libXt
432. [desktop] setxkbmap 1.3.4 | deps: libX11, libxkbfile
433. [desktop] xkbcomp 1.5.0 | deps: libX11, libxkbfile
434. [desktop] encodings 1.1.0 | deps: font-util, mkfontscale
435. [desktop] font-cursor-misc 1.0.4 | deps: bdftopcf, font-util, mkfontscale
436. [desktop] font-misc-misc 1.1.3 | deps: bdftopcf, font-util, mkfontscale
437. [desktop] libxkbcommon 1.13.1 | deps: libxcb, wayland, wayland-protocols, xkeyboard-config
438. [desktop] libXvMC 1.0.15 | deps: libXv
439. [desktop] libXtst 1.2.5 | deps: libXi
440. [desktop] poppler 26.02.0 | deps: cairo, fontconfig, libjpeg-turbo, libpng, nss, openjpeg2
441. [desktop] pycairo 1.29.0 | deps: cairo
442. [desktop] xcursorgen 1.0.9 | deps: libXcursor, libpng
443. [desktop] pango 1.57.0 | deps: cairo, fontconfig, freetype2, fribidi, glib2, gobject-introspection, harfbuzz, libX11, libXft
444. [desktop] libXpresent 1.0.2 | deps: libXfixes, libXrandr
445. [desktop] vulkan-loader 1.4.341.0 | deps: libX11, libXrandr, vulkan-headers, wayland
446. [desktop] xev 1.2.6 | deps: libX11, libXrandr
447. [desktop] xinput 1.6.4 | deps: libXi, libXinerama, libXrandr
448. [desktop] xrandr 1.5.3 | deps: libXrandr
449. [desktop] xauth 1.1.5 | deps: libXau, libXext, libXmu
450. [desktop] xhost 1.0.10 | deps: libX11, libXmu
451. [desktop] xrdb 1.2.2 | deps: libX11, libXmu
452. [desktop] xset 1.2.5 | deps: libXext, libXmu
453. [desktop] libXaw 1.0.16 | deps: libXmu, libXpm
454. [desktop] at-spi2-core 2.58.3 | deps: libX11, libXi, libXtst
455. [desktop] xdpyinfo 1.4.0 | deps: libXi, libXtst
456. [desktop] libcupsfilters 2.1.1 | deps: cups, lcms2, mupdf, poppler, qpdf
457. [desktop] pygobject3 3.54.5 | deps: pycairo
458. [desktop] xcursor-themes 1.0.7 | deps: xcursorgen
459. [desktop] graphviz 14.1.2 | deps: cairo, cmake, fontconfig, freetype2, libpng, pango
460. [desktop] gst-plugins-base 1.28.1 | deps: alsa-lib, gstreamer, libX11, libXext, libogg, libvorbis, orc, pango
461. [desktop] mesa 25.3.5 | deps: Mako, cbindgen, elfutils, expat, glslang, libX11, libXext, libXfixes, libXrandr, libclc, libdrm, libxcb, libxkbcommon, libxshmfence, llvm, rust-bindgen, vulkan-headers, vulkan-loader, wayland-protocols, zstd
462. [desktop] libppd 2.1.1 | deps: libcupsfilters
463. [desktop] blueprint-compiler 0.18.0 | deps: pygobject3
464. [desktop] power-profiles-daemon 0.30 | deps: polkit, pygobject3, upower
465. [desktop] vala 0.56.18 | deps: glib2, graphviz
466. [desktop] gst-plugins-bad 1.28.1 | deps: gst-plugins-base, soundtouch
467. [desktop] gst-plugins-good 1.28.1 | deps: cairo, flac, gst-plugins-base, lame, libpng, pulseaudio
468. [desktop] xdg-desktop-portal 1.20.3 | deps: bubblewrap, fuse3, gdk-pixbuf, gst-plugins-base, json-glib, pipewire
469. [desktop] gst-plugins-base-pass2 1.28.1 | deps: alsa-lib, gst-plugins-base, libX11, libXext, libogg, libvorbis, mesa, orc, pango
470. [desktop] libepoxy 1.5.10 | deps: mesa
471. [desktop] libva 2.23.0 | deps: libX11, libdrm, mesa, wayland
472. [desktop] xdriinfo 1.0.8 | deps: libX11, mesa
473. [desktop] cups-filters 2.0.1 | deps: libcupsfilters, libppd
474. [desktop] accountsservice 23.13.9 | deps: polkit, vala
475. [desktop] enchant 2.8.15 | deps: aspell, glib2, vala
476. [desktop] gexiv2 0.14.6 | deps: exiv2, pygobject3, vala
477. [desktop] libcloudproviders 0.3.6 | deps: glib2, vala
478. [desktop] libgusb 0.4.9 | deps: json-glib, libusb, vala
479. [desktop] libical 3.0.20 | deps: cmake, glib2, gobject-introspection, libxml2, vala
480. [desktop] librsvg 2.61.4 | deps: cairo, cargo-c, gdk-pixbuf, pango, vala
481. [desktop] libsecret 0.21.7 | deps: glib2, libgcrypt, libxslt, vala
482. [desktop] libsoup3 3.6.6 | deps: glib-networking, libpsl, libxml2, nghttp2, vala
483. [desktop] modemmanager 1.24.2 | deps: libgudev, libmbim, libqmi, polkit, vala
484. [desktop] networkmanager 1.56.0 | deps: iptables, libndp, libnl, newt, polkit, pygobject3, vala, wpa_supplicant
485. [desktop] gtk3 3.24.51 | deps: at-spi2-core, gdk-pixbuf, iso-codes, libXcomposite, libXcursor, libXdamage, libXi, libXinerama, libXrandr, libepoxy, libxkbcommon, pango, wayland, wayland-protocols
486. [desktop] xwayland 24.1.9 | deps: font-util, libX11, libXfont2, libepoxy, libxcvt, mesa, pixman, wayland-protocols, xkbcomp, xkeyboard-config
487. [desktop] colord 1.4.8 | deps: lcms2, libgudev, libgusb, polkit, systemd, vala
488. [desktop] bluez 5.86 | deps: libical
489. [desktop] glycin 2.0.8 | deps: bubblewrap, fontconfig, lcms2, libheif, librsvg, libseccomp, rust, vala
490. [desktop] gtk4 4.20.3 | deps: cairo, gdk-pixbuf, gobject-introspection, graphene, gst-plugins-base-pass2, iso-codes, libXcursor, libXi, libXrandr, libepoxy, librsvg, libxkbcommon, pango, pygobject3, shaderc, vulkan-loader, wayland-protocols
491. [desktop] geocode-glib 3.26.4 | deps: json-glib, libsoup3
492. [desktop] tinysparql 3.10.1 | deps: icu, json-glib, libsoup3, pygobject3, vala
493. [desktop] adwaita-icon-theme 49.0 | deps: gtk3, librsvg
494. [desktop] avahi 0.8 | deps: dbus, glib2, gtk3, libdaemon
495. [desktop] dconf 0.49.0 | deps: gtk3, vala
496. [desktop] gcr 3.41.2 | deps: gtk3, libgcrypt, libsecret, p11-kit, vala
497. [desktop] gnome-autoar 0.4.5 | deps: gtk3, vala
498. [desktop] libcanberra 0.30 | deps: alsa-lib, gtk3, libvorbis, sound-theme-freedesktop
499. [desktop] libhandy1 1.8.3 | deps: gtk3, vala
500. [desktop] libpeas 1.36.0 | deps: gtk3, pygobject3
501. [desktop] gdk-pixbuf-pass2 2.44.5 | deps: giflib, glycin, libjpeg-turbo, libpng, libtiff, shared-mime-info
502. [desktop] colord-gtk 0.3.1 | deps: colord, gtk3, gtk4, vala
503. [desktop] gcr4 4.4.0.1 | deps: gtk4, libgcrypt, libsecret, p11-kit, vala
504. [desktop] gjs 1.86.0 | deps: cairo, gtk3, gtk4, spidermonkey
505. [desktop] gnome-desktop 44.5 | deps: gsettings-desktop-schemas, gtk3, gtk4, iso-codes, libseccomp, xkeyboard-config
506. [desktop] gtksourceview5 5.18.0 | deps: gtk4
507. [desktop] libadwaita1 1.8.4 | deps: appstream, gtk4, sassc, vala
508. [desktop] libnotify 0.8.8 | deps: gdk-pixbuf, gtk4
509. [desktop] libportal 0.9.1 | deps: gtk3, gtk4
510. [desktop] libshumate 1.5.3 | deps: gtk4, libsoup3, protobuf-c
511. [desktop] mutter 49.4 | deps: argcomplete, at-spi2-core, glycin, graphene, gtk4, libX11, libXcomposite, libXdamage, libXfixes, libXi, libXrandr, libdisplay-info, libei, libinput, libxcvt, libxkbcommon, pipewire, startup-notification, wayland-protocols
512. [desktop] vte 0.82.3 | deps: fribidi, gnutls, gtk3, gtk4, icu, libxml2, vala
513. [desktop] libgweather 4.4.4 | deps: geocode-glib, libsoup3, pygobject3
514. [desktop] localsearch 3.10.2 | deps: gexiv2, gst-plugins-base, tinysparql
515. [desktop] nss-mdns 0.15.1 | deps: avahi
516. [desktop] ibus 1.5.33 | deps: dconf, gtk3, gtk4, iso-codes, libarchive, vala
517. [desktop] gnome-keyring 48.0 | deps: gcr, linux-pam
518. [desktop] libnma 1.10.6 | deps: gcr, gtk3, gtk4, iso-codes, networkmanager, vala
519. [desktop] gdm 49.2 | deps: accountsservice, dconf, gtk3, libcanberra, linux-pam
520. [desktop] gsound 1.0.3 | deps: libcanberra, vala
521. [desktop] gvfs 1.58.2 | deps: avahi, gcr4, glib-networking, gtk3, libbluray, libcdio-paranoia, libgphoto2, libimobiledevice, libmtp, libnfs, libsecret, libusb, udisks2
522. [desktop] gnome-session 49.2 | deps: gnome-desktop, json-glib, mesa, upower
523. [desktop] xdg-desktop-portal-gtk 1.15.3 | deps: gnome-desktop, gtk3, xdg-desktop-portal
524. [desktop] gnome-tweaks 49.0 | deps: gsettings-desktop-schemas, gtk4, libadwaita1, libgudev, pygobject3, sound-theme-freedesktop
525. [desktop] librest 0.10.2 | deps: gtksourceview5, json-glib, libadwaita1, libsoup3, make-ca
526. [desktop] tecla 49.0 | deps: libadwaita1, libxkbcommon
527. [desktop] geoclue2 2.8.0 | deps: json-glib, libnotify, libsoup3, vala
528. [desktop] nautilus 49.3 | deps: gexiv2, gnome-autoar, gnome-desktop, gst-plugins-base, libadwaita1, libportal, libseccomp, tinysparql
529. [desktop] gnome-terminal 3.58.1 | deps: dconf, gsettings-desktop-schemas, itstool, libadwaita1, libhandy1, vte
530. [desktop] gnome-bluetooth 47.1 | deps: gsound, gtk4, libadwaita1, upower
531. [desktop] xdg-desktop-portal-gnome 49.0 | deps: gnome-desktop, gtk4, libadwaita1, xdg-desktop-portal, xdg-desktop-portal-gtk
532. [desktop] gnome-online-accounts 3.56.4 | deps: gcr4, json-glib, libadwaita1, librest, vala
533. [desktop] gnome-settings-daemon 49.1 | deps: alsa-lib, colord, fontconfig, gcr4, geoclue2, geocode-glib, gnome-desktop, libcanberra, libgweather, libnotify, libwacom, networkmanager, pulseaudio, upower, wayland
534. [desktop] webkitgtk-gtk3 2.50.5 | deps: bubblewrap, cairo, enchant, geoclue2, gst-plugins-bad, gst-plugins-base, gtk3, icu, libX11, libgudev, libseccomp, libsoup3, libwebp, openjpeg2, ruby, unifdef, wayland, xdg-dbus-proxy
535. [desktop] libmsgraph 0.3.4 | deps: glib2, gnome-online-accounts, json-glib, libsoup3
536. [desktop] gnome-control-center 49.4 | deps: accountsservice, blueprint-compiler, colord-gtk, cups, gnome-bluetooth, gnome-online-accounts, gnome-settings-daemon, gsound, libadwaita1, libgtop, libnma, libpwquality, mitkrb, modemmanager, networkmanager, samba, shared-mime-info, tecla, udisks2
537. [desktop] webkitgtk 2.50.5 | deps: bubblewrap, cairo, enchant, geoclue2, gst-plugins-bad, gst-plugins-base, gtk3, gtk4, icu, libX11, libgudev, libseccomp, libsoup3, libwebp, openjpeg2, ruby, unifdef, wayland, webkitgtk-gtk3, xdg-dbus-proxy
538. [desktop] gvfs-pass2 1.58.2 | deps: glib-networking, gnome-online-accounts, gvfs, libmsgraph
539. [desktop] evolution-data-server 3.58.3 | deps: gnome-online-accounts, gtk3, gtk4, libgweather, libical, libsecret, nss, vala, webkitgtk
540. [desktop] gnome-shell 49.4 | deps: evolution-data-server, gcr4, gjs, gnome-desktop, gnome-settings-daemon, ibus, mutter, polkit, startup-notification
541. [desktop] gnome-shell-extensions 49.0 | deps: gnome-shell, libgtop
```

---

## Package Count Summary by Tier

- **toolchain**: 28 packages
- **core**: 107 packages
- **base**: 20 packages
- **desktop**: 382 packages
- **extra**: 4 packages
- **TOTAL**: 541 packages

---

## Build Style Distribution

- **custom**: 276 packages
- **autotools**: 200 packages
- **meson**: 48 packages
- **cmake**: 11 packages
- **make**: 6 packages

---

## Packages with skip_tracking or direct_install Flags

These flags affect how the package tracking system handles installation.
- `skip_tracking`: Package files are not recorded in the manifest database
- `direct_install`: Package installs directly to `/` instead of DESTDIR staging

- **gdk-pixbuf-pass2** 2.44.5: direct_install
- **linux-kernel-pass2** 6.18.10: skip_tracking, direct_install
- **pyyaml-pass2** 6.0.3: skip_tracking, direct_install
- **systemd-pass2** 259.1: skip_tracking, direct_install

---

## Packages with Validation Checks

These packages have post-build validation steps defined in their templates.

- **binutils-pass1** 2.46.0: sanity_check
- **gcc-pass1** 15.2.0: sanity_check
- **glibc** 2.43: sanity_check

---

**End of Part 5. This concludes the InterGenOS Full Systems Audit.**

**All 5 parts together constitute the complete audit:**
- Part 1: Master Orchestrator + Chroot Management Scripts
- Part 2: Build Phase Scripts (11 scripts)
- Part 3: Python Build System (18 files) + Package Functions + Image Creator + Host Checker
- Part 4: Kernel Configuration Files (3648 config options)
- Part 5: Complete Dependency Graph (541 packages)
