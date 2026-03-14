import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
XML_PATH = "C:/Projects/Video-pipeline/temp_prproj.xml"
OUT_PATH = "C:/Projects/Video-pipeline/prproj_analysis.txt"
print("Parsing XML...")
tree = ET.parse(XML_PATH)
root = tree.getroot()
print("Done. Building index...")
obj_map = {}
obj_uid_map = {}
for elem in root.iter():
    oid = elem.get("ObjectID")
    if oid: obj_map[oid] = elem
    ouid = elem.get("ObjectUID")
    if ouid: obj_uid_map[ouid] = elem
print(f"Index built: {len(obj_map)} ObjectIDs, {len(obj_uid_map)} ObjectUIDs")
TICKS = 254016000000
def tc(ticks, fps=25.0):
    if not ticks or ticks <= 0: return "00:00:00:00"
    ts = int(ticks) / TICKS
    h,m,s = int(ts//3600), int((ts%3600)//60), int(ts%60)
    fr = int(round((ts-int(ts))*fps))
    if fr >= int(fps): fr = int(fps)-1
    return f"{h:02d}:{m:02d}:{s:02d}:{fr:02d}"
def gt(e, tag, d=""):
    c = e.find(tag)
    if c is not None and c.text: return c.text.strip()
    return d
def rr(i): return obj_map.get(i)
def ru(u): return obj_uid_map.get(u)
outlines = []
def p(s=""):
    outlines.append(s)

p("="*72)
p("PREMIERE PRO PROJECT ANALYSIS")
p("File: temp_prproj.xml")
p("="*72)
p()
# Section 1: Metadata
p("## 1. PROJECT & SEQUENCE METADATA")
p("-"*72)
seq = None
for e in root.iter():
    if e.get("ObjectUID") == "0fc8bc0e-9129-45c0-b135-101117599faf" and e.get("ClassID"):
        seq = e; break
if seq:
    p("Sequence Name:  " + gt(seq, "Name"))
    p("Sequence UID:   0fc8bc0e-9129-45c0-b135-101117599faf")
    p("Class Version:  " + str(seq.get("Version")))

vtg = obj_map.get("1151")
if vtg is not None:
    fr2 = gt(vtg,"FrameRect")
    pts = fr2.split(",") if fr2 else []
    if len(pts)==4: p("Resolution:     " + pts[2] + " x " + pts[3])
    fre = vtg.find(".//TrackGroup/FrameRate")
    if fre is not None and fre.text:
        frt = int(fre.text.strip())
        fps2 = TICKS/frt if frt else 0
        p("Frame Rate:     " + str(round(fps2,3)) + " fps  (" + str(frt) + " ticks/frame)")
    cms = gt(vtg,"ColorManagementSettings")
    if cms:
        try: p("Color Mgmt:     " + str(json.loads(cms)))
        except: p("Color Mgmt:     " + cms)
    ocs = gt(vtg,"OutputColorSpace")
    if ocs:
        try: p("Output CS:      " + str(json.loads(ocs)))
        except: pass
    p("ToneMap Desat:  " + gt(vtg,"ToneMappingDesaturation"))
    p("AutoGamutComp:  " + gt(vtg,"AutoInputGamutCompressionEnabled"))
p()

# Section 2: get_clip_info helper
def get_clip_info(obj_id):
    ie = rr(obj_id)
    if ie is None: return None
    info = {"obj_id": obj_id, "tag": ie.tag}
    ti = ie.find(".//TrackItem")
    if ti is not None:
        st = gt(ti,"Start"); en = gt(ti,"End")
        if st: info["start_ticks"] = int(st); info["start_tc"] = tc(int(st))
        if en: info["end_ticks"] = int(en); info["end_tc"] = tc(int(en))
        if st and en: info["duration_sec"] = (int(en)-int(st))/TICKS
    for sc_ref in ie.findall(".//SubClip"):
        sc_id = sc_ref.get("ObjectRef")
        if sc_id:
            sc = rr(sc_id)
            if sc is not None:
                nm = gt(sc,"Name")
                if nm: info["name"] = nm
                cl = sc.find("Clip")
                if cl is not None:
                    cid = cl.get("ObjectRef")
                    if cid:
                        ce = rr(cid)
                        if ce is not None:
                            ci = ce.find("Clip")
                            if ci is not None:
                                ip = gt(ci,"InPoint"); op = gt(ci,"OutPoint")
                                if ip: info["in_tc"] = tc(int(ip))
                                if op: info["out_tc"] = tc(int(op))
                                sr = ci.find("Source")
                                if sr is not None:
                                    sid = sr.get("ObjectRef")
                                    if sid:
                                        se = rr(sid)
                                        if se is not None:
                                            ms = se.find("MediaSource")
                                            if ms is not None:
                                                med = ms.find("Media")
                                                if med is not None:
                                                    mu = med.get("ObjectURef")
                                                    if mu:
                                                        me = ru(mu)
                                                        if me is not None:
                                                            fp = gt(me,"FilePath") or gt(me,"RelativePath")
                                                            if fp: info["file_path"] = fp
                                                            t2 = gt(me,"Title")
                                                            if t2: info["media_title"] = t2
        break
    return info

# Section 2: Video tracks
p("## 2. VIDEO TRACKS & CLIPS ON TIMELINE")
p("-"*72)

VT_UIDS = [
    "9338fad4-9861-4eb4-a01a-6923671e34c0",
    "56edb740-0b1e-4c19-83d5-f09e43ea99ef",
    "87c2ed59-8680-413c-8141-b605316afa6a",
    "ef56aa15-9c3b-42c1-98c7-1a06f3c84e0e"
]

all_clips = []
for ti_idx, uid in enumerate(VT_UIDS):
    te = ru(uid)
    if te is None: continue
    ct = te.find("ClipTrack")
    if ct is None: continue
    trk = ct.find("Track")
    tid = gt(trk,"ID") if trk is not None else "?"
    locked = gt(trk,"IsLocked") if trk is not None else "?"
    muted = gt(trk,"IsMuted") if trk is not None else "?"
    ci2 = ct.find("ClipItems")
    tc2 = ci2.find("TrackItems") if ci2 is not None else None
    refs = [r.get("ObjectRef") for r in (tc2 if tc2 is not None else []) if r.get("ObjectRef")]
    tr_it = ct.find("TransitionItems")
    trans = [r.get("ObjectRef") for r in (tr_it if tr_it is not None else []) if r.get("ObjectRef")]
    p(f"### Video Track V{ti_idx+1}  (TrackID={tid}, Locked={locked}, Muted={muted})")
    p(f"    Clips: {len(refs)}    Transitions: {len(trans)}")
    p(f"    {chr(35):<4} {\"Name\":<40} {\"Start\":<17} {\"End\":<17} {\"Dur(s)\":<8} {\"In\":<13} {\"Out\":<13} File")
    p("    " + "-"*145)
    for ri, rid in enumerate(refs):
        info = get_clip_info(rid)
        if info is not None:
            all_clips.append({**info, "track": f"V{ti_idx+1}"})
            nm = info.get("name", info.get("media_title","?"))
            stc2 = info.get("start_tc","?")
            etc2 = info.get("end_tc","?")
            dur = info.get("duration_sec",0)
            fp2 = info.get("file_path","")
            fn = fp2.replace("\\\\","/").split("/")[-1] if fp2 else "?"
            ipt = info.get("in_tc","--")
            opt = info.get("out_tc","--")
            p(f"    {ri:<4} {nm:<40} {stc2:<17} {etc2:<17} {dur:<8.2f} {ipt:<13} {opt:<13} {fn}")
    if trans:
        p("    Transitions:")
        for tref in trans:
            te2 = rr(tref)
            if te2 is not None:
                ti3 = te2.find(".//TrackItem")
                if ti3 is not None:
                    ts2 = gt(ti3,"Start"); te3 = gt(ti3,"End")
                    if ts2 and te3:
                        dt = (int(te3)-int(ts2))/TICKS
                        p(f"      {tc(int(ts2))} -> {tc(int(te3))}  dur={dt:.2f}s  [obj={tref}]")
    p()

p(f"TOTAL VIDEO CLIPS: {len(all_clips)}")
p()
# Section 3: Audio tracks
p("## 3. AUDIO TRACKS & CLIPS")
p("-"*72)

audio_clip_tracks = list(root.findall(".//AudioClipTrack"))
p(f"AudioClipTrack elements: {len(audio_clip_tracks)}")
p()

def get_audio_clip_info(obj_id):
    ie = rr(obj_id)
    if ie is None: return {}
    info = {}
    ti = ie.find(".//TrackItem")
    if ti is not None:
        st = gt(ti,"Start"); en = gt(ti,"End")
        if st: info["start_tc"] = tc(int(st))
        if en: info["end_tc"] = tc(int(en))
        if st and en: info["dur"] = (int(en)-int(st))/TICKS
    for sc_ref in ie.findall(".//SubClip"):
        sc_id = sc_ref.get("ObjectRef")
        if sc_id:
            sc = rr(sc_id)
            if sc is not None:
                nm = gt(sc,"Name")
                if nm: info["name"] = nm
                cl = sc.find("Clip")
                if cl is not None:
                    cid = cl.get("ObjectRef")
                    if cid:
                        ce = rr(cid)
                        if ce is not None:
                            ci = ce.find("Clip")
                            if ci is not None:
                                sr = ci.find("Source")
                                if sr is not None:
                                    sid = sr.get("ObjectRef")
                                    if sid:
                                        se = rr(sid)
                                        if se is not None:
                                            ms = se.find("MediaSource")
                                            if ms is not None:
                                                med = ms.find("Media")
                                                if med is not None:
                                                    mu = med.get("ObjectURef")
                                                    if mu:
                                                        me = ru(mu)
                                                        if me is not None:
                                                            fp = gt(me,"FilePath") or gt(me,"RelativePath")
                                                            if fp: info["file_path"] = fp
        break
    return info

all_audio_clips = []
for ai, act in enumerate(audio_clip_tracks):
    ct2 = act.find("ClipTrack")
    if ct2 is None: continue
    trk2 = ct2.find("Track")
    tid2 = gt(trk2,"ID") if trk2 is not None else "?"
    muted2 = gt(trk2,"IsMuted") if trk2 is not None else "?"
    ci3 = ct2.find("ClipItems")
    tc3 = ci3.find("TrackItems") if ci3 is not None else None
    refs2 = [r.get("ObjectRef") for r in (tc3 if tc3 is not None else []) if r.get("ObjectRef")]
    p(f"### Audio Track A{ai+1}  (TrackID={tid2}, Muted={muted2}) - {len(refs2)} clips")
    p(f"    {chr(35):<4} {\"Name\":<40} {\"Start\":<17} {\"End\":<17} {\"Dur(s)\":<9} File")
    p("    " + "-"*120)
    for ri2, rid2 in enumerate(refs2):
        info2 = get_audio_clip_info(rid2)
        nm2 = info2.get("name","?")
        stc2 = info2.get("start_tc","?")
        etc2 = info2.get("end_tc","?")
        dur2 = info2.get("dur",0)
        fp3 = info2.get("file_path","")
        fn2 = fp3.replace("\\\\","/").split("/")[-1] if fp3 else "?"
        p(f"    {ri2:<4} {nm2:<40} {stc2:<17} {etc2:<17} {dur2:<9.2f} {fn2}")
        all_audio_clips.append(info2)
    p()

p(f"TOTAL AUDIO CLIPS: {len(all_audio_clips)}")
p()
# Section 4: Media files
p("## 4. ALL MEDIA FILES REFERENCED")
p("-"*72)

media_files = {}
for elem in root.iter():
    if elem.tag == "Media" and elem.get("ObjectUID"):
        fp4 = gt(elem,"FilePath") or gt(elem,"ActualMediaFilePath")
        title4 = gt(elem,"Title")
        rp4 = gt(elem,"RelativePath")
        uid4 = elem.get("ObjectUID")
        if fp4 or title4:
            media_files[uid4] = {"file_path": fp4, "title": title4, "relative_path": rp4}

p(f"Total media files: {len(media_files)}")
p()
p(f"{\"Title\":<50} {\"Relative Path\":<35} Full Path")
p("-"*140)
for uid5, mf in sorted(media_files.items(), key=lambda x: x[1].get("title","").lower()):
    p(f"{mf.get(\"title\",\"?\"):<50} {mf.get(\"relative_path\",\"\"):<35} {mf.get(\"file_path\",\"\")}")
p()
# Section 5: Effects
p("## 5. VIDEO EFFECTS APPLIED TO CLIPS")
p("-"*72)

vfc_names = []
for e in root.iter():
    if e.tag == "VideoFilterComponent":
        mn = e.find("MatchName")
        if mn is not None and mn.text: vfc_names.append(mn.text.strip())

vfc_counter = Counter(vfc_names)
p("Video Effects (MatchName, sorted by count):")
for name, cnt in vfc_counter.most_common():
    p(f"  {cnt:5d}x  {name}")
p()

afc_names = []
for e in root.iter():
    if e.tag == "AudioFilterComponent":
        mn = e.find("FilterMatchName")
        if mn is not None and mn.text: afc_names.append(mn.text.strip())

afc_counter = Counter(afc_names)
p("Audio Effects (FilterMatchName, sorted by count):")
for name, cnt in afc_counter.most_common():
    p(f"  {cnt:5d}x  {name}")
p()

# Section 6: Lumetri Color
p("## 6. LUMETRI COLOR / COLOR CORRECTION")
p("-"*72)

lumetri_instances = []
for e in root.iter():
    if e.tag == "VideoFilterComponent":
        mn = e.find("MatchName")
        if mn is not None and mn.text and "Lumetri" in mn.text:
            lumetri_instances.append(e)

p(f"Total Lumetri Color instances: {len(lumetri_instances)}")
p()
if lumetri_instances:
    p("Lumetri parameters (first instance):")
    first_l = lumetri_instances[0]
    params_l = first_l.findall(".//Param")
    for param in params_l[:40]:
        pid = gt(param,"ParameterID")
        cv_e = param.find("CurrentValue")
        cv = cv_e.text.strip()[:80] if cv_e is not None and cv_e.text else ""
        ptype = gt(param,"ParameterControlType")
        if pid: p(f"  {pid:<50} type={ptype:<6} value={cv}")
p()
# Section 7: Text/Graphics
p("## 7. TEXT / GRAPHICS LAYERS")
p("-"*72)

content_list = []
for e in root.findall(".//FormattedTextData"):
    ce = e.find("Content")
    if ce is not None and ce.text:
        txt = ce.text.strip()
        if txt and txt not in content_list: content_list.append(txt)

p(f"Unique text content items: {len(content_list)}")
p()
for i, txt in enumerate(content_list):
    p(f"  [{i:3d}] {txt[:200]}")
p()

adj_list = list(root.findall(".//AdjustmentLayer"))
p(f"AdjustmentLayer elements: {len(adj_list)}")

mgt_list = list(root.findall(".//MotionGraphicsTemplateInstance"))
p(f"MotionGraphicsTemplateInstance: {len(mgt_list)}")
p()

# Section 8: Captions
p("## 8. CAPTIONS / SUBTITLES")
p("-"*72)

dcti = list(root.findall(".//DataClipTrackItem"))
p(f"DataClipTrackItem: {len(dcti)}")

cap_items = list(root.findall(".//CaptionDataClipTrackItem"))
p(f"CaptionDataClipTrackItem: {len(cap_items)}")

trans_clips = list(root.findall(".//TranscriptClip"))
p(f"TranscriptClip elements: {len(trans_clips)}")

block_items = list(root.findall(".//BlockVectorItem"))
p(f"BlockVectorItem (caption blocks): {len(block_items)}")
p()

if block_items:
    p("Caption blocks (first 40):")
    for bi in block_items[:40]:
        st_b = gt(bi,"Start"); en_b = gt(bi,"End")
        tc_s = tc(int(st_b)) if st_b else "?"
        tc_e = tc(int(en_b)) if en_b else "?"
        ce2 = bi.find(".//Content")
        txt2 = ce2.text.strip()[:120] if ce2 is not None and ce2.text else ""
        p(f"  {tc_s} -> {tc_e}: {txt2}")
    p()
# Section 9: Export settings
p("## 9. EXPORT / COMPILE SETTINGS")
p("-"*72)

compile_settings = [e for e in root if e.tag == "CompileSettings"]
p(f"CompileSettings: {len(compile_settings)}")
for i, cs in enumerate(compile_settings):
    p(f"  CompileSettings [{i}]: ClassID={cs.get(\"ClassID\")} Version={cs.get(\"Version\")}")
    for child in cs:
        if child.text and child.text.strip():
            p(f"    {child.tag}: {child.text.strip()[:200]}")
        for sub in child[:5]:
            if sub.text and sub.text.strip():
                p(f"    {child.tag}/{sub.tag}: {sub.text.strip()[:200]}")
p()

# Section 10: Bin structure
p("## 10. BIN / FOLDER STRUCTURE")
p("-"*72)

bins = [e for e in root.iter() if e.tag == "BinProjectItem" and e.get("ObjectUID")]
p(f"BinProjectItem: {len(bins)}")
for b in bins:
    nm_b = gt(b,"Name")
    uid_b = b.get("ObjectUID")
    p(f"  BIN: {nm_b:<40} UID={uid_b}")
p()
# Section 11: Motion / Keyframe analysis
p("## 11. MOTION & KEYFRAME ANALYSIS")
p("-"*72)

all_kf = list(root.findall(".//StartKeyframe"))
p(f"StartKeyframe elements: {len(all_kf)}")

param_names_with_kf = Counter()
for e in root.iter():
    if e.tag == "VideoComponentParam":
        tv = gt(e,"IsTimeVarying")
        if tv == "true":
            pid2 = gt(e,"ParameterID")
            if pid2: param_names_with_kf[pid2] += 1

p("Parameters with keyframes (IsTimeVarying=true):")
for nm3, cnt3 in param_names_with_kf.most_common(20):
    p(f"  {cnt3:5d}x  {nm3}")
p()

# Section 12: Summary
p("## 12. SUMMARY")
p("="*72)

max_end = max((c.get("end_ticks",0) for c in all_clips), default=0)
total_sec = max_end/TICKS if max_end else 0

p(f"Project name:              Video 66 done")
p(f"Resolution:                1920 x 1080")
p(f"Frame rate:                25.000 fps")
p(f"Total video tracks:        4 (V1-V4)")
p(f"Total audio tracks:        {len(audio_clip_tracks)}")
p(f"Total video clips (V1):    {sum(1 for c in all_clips if c[\"track\"]==\"V1\")}")
p(f"Total video clips (V2):    {sum(1 for c in all_clips if c[\"track\"]==\"V2\")}")
p(f"Total video clips (V3):    {sum(1 for c in all_clips if c[\"track\"]==\"V3\")}")
p(f"Total video clips (V4):    {sum(1 for c in all_clips if c[\"track\"]==\"V4\")}")
p(f"Total video clips ALL:     {len(all_clips)}")
p(f"Total audio clips:         {len(all_audio_clips)}")
p(f"Total media files:         {len(media_files)}")
p(f"Lumetri Color instances:   {len(lumetri_instances)}")
p(f"Unique text layers:        {len(content_list)}")
p(f"Adjustment layers:         {len(adj_list)}")
p(f"DataClipTrackItems:        {len(dcti)}")
p(f"CaptionDataClipTrackItems: {len(cap_items)}")
if max_end:
    p(f"Sequence duration:         {total_sec:.2f}s = {total_sec/60:.2f} min (last clip at {tc(max_end)})")
p()
p("--- Top Video Effects ---")
for nm4, cnt4 in vfc_counter.most_common(10):
    p(f"  {cnt4:5d}x  {nm4}")
p()
p("--- Top Audio Effects ---")
for nm4, cnt4 in afc_counter.most_common(10):
    p(f"  {cnt4:5d}x  {nm4}")
p()
p(f"Total keyframe elements:   {len(all_kf)}")
p()

# Write output
with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(outlines))

print(f"\nAnalysis complete. Output: {OUT_PATH}")
print(f"Lines written: {len(outlines)}")
