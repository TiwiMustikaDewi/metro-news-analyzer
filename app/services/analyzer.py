import io
import re
import json
import difflib
import requests
import cloudscraper
import imagehash
import numpy as np
from PIL import Image
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone

from app.models.models import Article, SimilarityResult
from app.schemas.analyzer import AnalyzeResponse, TextSimilarityResult

# =============================================================================
# CACHE VEKTOR ARTIKEL RESMI (In-Memory)
# Vektor di-encode sekali saat pertama kali dibutuhkan, lalu disimpan di memori.
# Cache akan di-invalidate jika jumlah artikel berubah.
# =============================================================================
_official_vectors_cache = {
    "vectors": None,
    "article_ids": None,
}

# =============================================================================
# MODEL LOADING
# Model di-load sekali saat modul ini pertama kali diimport (saat server start).
# =============================================================================
print("[INFO] Memuat model AI Sentence Transformer (paraphrase-multilingual-MiniLM-L12-v2)...")
_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
print("[OK] Model AI berhasil dimuat dan siap digunakan!")

# Daftar kata umum / stopwords yang sering muncul di berita pemerintahan untuk diabaikan
STOPWORDS_NEWS = {
    "kota", "metro", "pemerintah", "pemkot", "dinas", "komunikasi", "informatika", "statistik",
    "diskominfotik", "walikota", "wali", "wakil", "gubernur", "lampung", "dprd", "rapat", "paripurna",
    "hari", "ini", "rabu", "kamis", "jumat", "sabtu", "minggu", "senin", "selasa", "januari", "februari",
    "maret", "april", "mei", "juni", "juli", "agustus", "september", "oktober", "novembr", "desember",
    "2024", "2025", "2026", "yang", "dan", "di", "ke", "dari", "pada", "adalah", "dengan", "untuk",
    "pada", "oleh", "dalam", "akan", "juga", "dapat", "tersebut", "bisa", "bahwa", "serta", "karena"
}


def _encode_text(text: str) -> np.ndarray:
    """Mengubah teks menjadi vektor embedding menggunakan model AI."""
    return _model.encode([text])[0]

def check_clickbait(title: str, content: str) -> bool:
    """
    Mendeteksi potensi clickbait dengan membandingkan kemiripan semantik 
    antara judul berita dan isi berita.
    """
    if not title or not content:
        return False
    title_vec = _encode_text(title)
    content_vec = _encode_text(content)
    similarity = cosine_similarity([title_vec], [content_vec])[0][0]
    return float(similarity * 100) < 25.0

# In-memory cache untuk gambar agar tidak mendownload gambar resmi yang sama berulang kali
_image_hash_cache = {}

def _get_image_hash(image_url: str) -> Optional[imagehash.ImageHash]:
    """Mengunduh gambar dari URL dan menghitung perceptual hash-nya."""
    if not image_url:
        return None
    if image_url in _image_hash_cache:
        return _image_hash_cache[image_url]
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.google.com/"
        }
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
        response = scraper.get(image_url, headers=headers, timeout=10, stream=True)
        response.raise_for_status()
        img = Image.open(io.BytesIO(response.content)).convert("RGB")
        h = imagehash.phash(img)
        _image_hash_cache[image_url] = h
        return h
    except Exception:
        _image_hash_cache[image_url] = None
        return None


def _calculate_image_similarity(hash1: imagehash.ImageHash, hash2: imagehash.ImageHash) -> float:
    """
    Menghitung persentase kemiripan gambar berdasarkan perbedaan hash (Hamming Distance).
    - Perbedaan 0 = gambar identik (100%)
    - Perbedaan 64 = gambar sangat berbeda (0%)
    """
    max_diff = 64
    diff = hash1 - hash2
    similarity = max(0.0, (max_diff - diff) / max_diff) * 100
    return round(similarity, 2)


def _split_sentences(text: str) -> List[str]:
    """Memisahkan teks menjadi kalimat-kalimat bermakna (min 35 karakter)."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if len(s.strip()) >= 35]


def _extract_keywords(text: str) -> set:
    """Mengambil kata-kata kunci unik bermakna dari teks, mengabaikan stopwords umum."""
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    return {w for w in words if w not in STOPWORDS_NEWS}


def _calculate_keyword_overlap(target_text: str, candidate_text: str) -> float:
    """
    Menghitung Jaccard similarity kata kunci unik (content words).
    Mencegah berita beda topik dianggap mirip hanya karena memiliki nama kota/jabatan yang sama.
    """
    kw_target = _extract_keywords(target_text)
    kw_cand = _extract_keywords(candidate_text)

    if not kw_target or not kw_cand:
        return 0.0

    intersection = kw_target.intersection(kw_cand)
    union = kw_target.union(kw_cand)
    
    return len(intersection) / len(union) if union else 0.0


def _calibrate_similarity(raw_cos_sim: float) -> float:
    """
    KALIBRASI PRESISI MODEL MULTILINGUAL EMBEDDING:
    Vektor embedding multilingual secara alami memiliki baseline similarity 0.40 - 0.48
    antara artikel berita dalam domain/bahasa yang sama.
    
    Fungsi ini mengalibrasi nilai 0.40 (baseline netral/beda topik) menjadi 0%, 
    dan merentangkan nilai 0.40 - 1.00 secara linier menjadi 0% - 100% skor kemiripan sebenarnya.
    """
    baseline_floor = 0.45  # Nilai ambang bawah berita beda topik dalam bahasa Indonesia domain lokal
    if raw_cos_sim <= baseline_floor:
        return 0.0
    
    calibrated = ((raw_cos_sim - baseline_floor) / (1.0 - baseline_floor)) * 100.0
    return round(max(0.0, min(100.0, calibrated)), 2)


def _calculate_sentence_level_similarity(target_text: str, candidate_text: str) -> tuple[float, List[dict]]:
    """
    Menghitung kemiripan gabungan (Semantik + Leksikal).
    Semantik tinggi & Leksikal tinggi = Copas mentah (Skor Tinggi).
    Semantik tinggi & Leksikal rendah = Parafrase bagus (Skor Diturunkan).
    """
    target_sentences = _split_sentences(target_text)
    candidate_sentences = _split_sentences(candidate_text)

    if not target_sentences or not candidate_sentences:
        return 0.0, []

    target_sentences = target_sentences[:30]
    candidate_sentences = candidate_sentences[:30]

    all_target_vecs = _model.encode(target_sentences, batch_size=32, show_progress_bar=False)
    all_cand_vecs = _model.encode(candidate_sentences, batch_size=32, show_progress_bar=False)

    sim_matrix = cosine_similarity(all_target_vecs, all_cand_vecs)

    matched_pairs = []
    final_sentence_scores = []

    for i, t_sent in enumerate(target_sentences):
        best_j = int(np.argmax(sim_matrix[i]))
        semantic_raw = float(sim_matrix[i][best_j])
        semantic_score = _calibrate_similarity(semantic_raw)
        
        c_sent = candidate_sentences[best_j]
        
        # Jika semantik cukup tinggi (topik sama), hitung kemiripan leksikal (copas kata-kata)
        if semantic_score >= 35.0:
            # Hitung Leksikal (Seberapa mirip kata per kata)
            sm = difflib.SequenceMatcher(None, t_sent.lower(), c_sent.lower())
            lexical_score = sm.ratio() * 100.0
            
            # Penggabungan: Jika lexical tinggi (copas persis), penalty berat. 
            # Jika lexical rendah (parafrase), skor dikurangi.
            # PENGAMPUNAN PARAFRASE: Kita kuadratkan lexical score.
            # Jika lexical 90% (Copas), nilainya tetap tinggi (81%).
            # Jika lexical 50% (Parafrase dgn nama tokoh sama), nilainya anjlok jadi 25%.
            # Ini akan sangat melindungi wartawan yang melakukan parafrase!
            lexical_penalty = ((lexical_score / 100.0) ** 2) * 100.0
            
            # Bobot: 85% Leksikal (Fokus Plagiat Kata) + 15% Semantik (Fokus Makna)
            combined_score = (lexical_penalty * 0.85) + (semantic_score * 0.15)
            
            final_sentence_scores.append(combined_score)
            
            matched_pairs.append({
                "target_sentence": t_sent,
                "matched_sentence": c_sent,
                "score": round(combined_score, 2),
                "semantic": round(semantic_score, 2),
                "lexical": round(lexical_score, 2)
            })
        else:
            final_sentence_scores.append(semantic_score * 0.3) # Kalau topik beda, skor pasti rendah

    matched_pairs.sort(key=lambda x: x["score"], reverse=True)
    
    # Rata-rata skor kalimat akhir
    sentence_score = float(np.mean(final_sentence_scores)) if final_sentence_scores else 0.0

    return round(sentence_score, 2), matched_pairs[:5]


def _calculate_time_penalty(target_date: Optional[datetime], official_date: Optional[datetime]) -> float:
    """
    Menghitung penalti selisih tanggal terbit berita.
    Berita resmi dan berita swasta yang diterbitkan pada hari/minggu yang sama kemungkinan besar
    merupakan penulisan ulang (parafrase). Sebaliknya selisih tanggal yang jauh diberikan penalti.
    """
    if not target_date or not official_date:
        return 0.85

    if target_date.tzinfo is not None:
        target_date = target_date.replace(tzinfo=None)
    if official_date.tzinfo is not None:
        official_date = official_date.replace(tzinfo=None)

    diff_days = abs((target_date - official_date).days)

    if diff_days <= 3:
        return 1.0    # 0-3 hari: sangat relevan, kemungkinan besar liputan kegiatan yang sama
    elif diff_days <= 14:
        return 0.85   # 2 minggu: masih relevan
    elif diff_days <= 45:
        return 0.65   # 1.5 bulan: mungkin topik terkait
    elif diff_days <= 180:
        return 0.40   # 6 bulan: topik sama tapi beda periode, penalti cukup besar
    elif diff_days <= 365:
        return 0.20   # 1 tahun: hampir pasti beda event, penalti sangat besar
    else:
        return 0.08   # > 1 tahun: dipastikan beda event, skor mendekati nol


def analyze_article(db: Session, article_id: int, top_n: int = 5) -> AnalyzeResponse:
    """
    Sistem Analisis Presisi Orisinalitas Berita:
    
    1. Embedding Semantik Terkalibrasi (Mengabaikan baseline topik umum)
    2. Overlap Kata Kunci Konten (Memastikan berita tentang substansi yang sama)
    3. Analisis Kemiripan Tingkat Kalimat (Sentence-level matching)
    4. Penalti Selisih Tanggal Terbit
    """
    # 1. Ambil artikel target
    target_article = db.query(Article).filter(Article.id == article_id).first()
    if not target_article:
        raise ValueError(f"Artikel dengan ID {article_id} tidak ditemukan.")

    # 2. Ambil artikel resmi pemerintah sebagai pembanding
    official_articles = (
        db.query(Article)
        .filter(Article.source_id == 1, Article.id != article_id)
        .all()
    )
    if not official_articles:
        return AnalyzeResponse(
            target_article_id=target_article.id,
            target_article_title=target_article.title,
            target_article_url=target_article.url,
            total_compared=0,
            results=[]
        )

    # 3. Encode teks artikel target
    target_vector = _encode_text(target_article.content)

    # 4. Encode semua artikel resmi (gunakan cache jika tersedia)
    current_ids = tuple(a.id for a in official_articles)
    if _official_vectors_cache["article_ids"] != current_ids or _official_vectors_cache["vectors"] is None:
        print(f"[CACHE MISS] Encoding {len(official_articles)} artikel resmi ke vektor...")
        all_contents = [a.content for a in official_articles]
        all_vectors = _model.encode(all_contents, batch_size=64, show_progress_bar=False)
        _official_vectors_cache["vectors"] = all_vectors
        _official_vectors_cache["article_ids"] = current_ids
        print("[CACHE SET] Vektor artikel resmi berhasil disimpan di memori.")
    else:
        print("[CACHE HIT] Menggunakan vektor artikel resmi dari cache memori.")
        all_vectors = _official_vectors_cache["vectors"]

    # 5. Hitung Raw Cosine Similarity & Kalibrasi Dokumen
    doc_raw_similarities = cosine_similarity([target_vector], all_vectors)[0]

    prescored = []
    for i, article in enumerate(official_articles):
        raw_doc_sim = float(doc_raw_similarities[i])
        calibrated_doc_score = _calibrate_similarity(raw_doc_sim)
        
        # Overlap Kata Kunci (Jaccard content words)
        kw_overlap = _calculate_keyword_overlap(target_article.content, article.content)
        
        time_penalty = _calculate_time_penalty(target_article.published_at, article.published_at)
        
        # Pre-ranking score
        pre_score = (calibrated_doc_score * 0.6 + (kw_overlap * 100) * 0.4) * time_penalty
        prescored.append((pre_score, calibrated_doc_score, kw_overlap, article, time_penalty))

    prescored.sort(key=lambda x: x[0], reverse=True)
    top_candidates = prescored[:min(top_n * 2, 15)]  # Optimal: lebih luas dari 10, lebih efisien dari 20

    # Pindahkan target_image_hash keluar dari loop agar tidak di-download berkali-kali!
    target_image_hash = None
    if target_article.image_url:
        target_image_hash = _get_image_hash(target_article.image_url)

    # 6. Analisis tingkat kalimat mendalam untuk Top kandidat
    final_results = []
    for pre_score, doc_score, kw_overlap, article, time_penalty in top_candidates:
        sentence_score, raw_matched_pairs = _calculate_sentence_level_similarity(
            target_article.content, article.content
        )

        matched_pairs = []
        quotes_excluded = 0
        reasons = []

        # Pisahkan kutipan
        for pair in raw_matched_pairs:
            ts = pair["target_sentence"]
            # Cek jika kalimat diapit oleh tanda kutip ganda
            if (ts.startswith('"') and ts.endswith('"')) or (ts.startswith('“') and ts.endswith('”')):
                pair["is_quote"] = True
                quotes_excluded += 1
            else:
                pair["is_quote"] = False
            
            matched_pairs.append(pair)

        # Hitung jumlah kalimat valid (non-kutipan)
        valid_matches = [p for p in matched_pairs if not p["is_quote"]]

        # === FORMULA SKOR PRESISI AKHIR SINKRON ===
        if not valid_matches:
            # Jika tidak ada kalimat spesifik non-kutipan yang mirip, TEKAN SKOR MAKSIMAL 15%
            final_score = round(min(doc_score * 0.2, 15.0) * time_penalty, 2)
            reasons.append("Skor rendah: Tidak ditemukan kalimat spesifik yang menjiplak di luar kutipan.")

            if quotes_excluded > 0:
                reasons.append(f"Ditemukan {quotes_excluded} kalimat mirip, tetapi terdeteksi sebagai kutipan sah dari tokoh.")
            if doc_score >= 40.0:
                reasons.append("Topik berita secara umum serupa, tetapi ditulis ulang secara orisinal (parafrase).")
        else:
            # Ada kalimat yang spesifik mirip (non-kutipan)
            # SKOR PLAGIARISME MURNI DARI KEMIRIPAN KALIMAT (COPAS). Doc Score tidak lagi ditambahkan agar topik beda/parafrase murni mendapat skor rendah.
            raw_weighted = sentence_score
            final_score = round(raw_weighted * time_penalty, 2)

            if final_score >= 40.0:
                reasons.append(f"Skor Tinggi (Peringatan): Ditemukan {len(valid_matches)} kalimat yang sangat mirip pada rentang waktu yang berdekatan.")
                if kw_overlap > 0.4:
                    reasons.append("Penggunaan kata kunci (vocabulary) sangat identik.")
                if quotes_excluded > 0:
                    reasons.append(f"Terdapat tambahan {quotes_excluded} kutipan sah yang dikecualikan dari penilaian utama.")
            elif final_score >= 20.0:
                if time_penalty < 1.0:
                    reasons.append(f"Ditemukan {len(valid_matches)} kalimat mirip secara struktur, tetapi penalti selisih tanggal terbit menurunkan skor secara signifikan.")
                else:
                    reasons.append(f"Ditemukan {len(valid_matches)} kalimat mirip secara struktur, tetapi tingkat kemiripan belum cukup tinggi untuk dianggap plagiasi murni.")
            else:
                if time_penalty < 0.5:
                    reasons.append(f"Ditemukan {len(valid_matches)} kalimat dengan pola serupa, tetapi selisih tanggal terbit yang sangat jauh menandakan peristiwa berbeda.")
                else:
                    reasons.append(f"Ditemukan {len(valid_matches)} kalimat dengan topik serupa, tetapi secara struktur ditulis ulang secara orisinal (parafrase kuat).")

            # === URAIAN KALIMAT DETAIL: Sebutkan masing-masing kalimat yang terdeteksi mirip ===
            for idx, vm in enumerate(valid_matches):
                t_short = vm["target_sentence"][:120] + ("..." if len(vm["target_sentence"]) > 120 else "")
                m_short = vm["matched_sentence"][:120] + ("..." if len(vm["matched_sentence"]) > 120 else "")
                reasons.append(
                    f"[Kalimat {idx+1} | Skor: {vm['score']}%] "
                    f"Berita Anda: \"{t_short}\" -- "
                    f"Berita Resmi: \"{m_short}\""
                )

            if quotes_excluded > 0:
                reasons.append(f"Catatan: {quotes_excluded} pasang kalimat lain terdeteksi sebagai kutipan tokoh dan dikecualikan dari penilaian.")

        # Menambahkan perbandingan tanggal upload ke reasons
        t_date_str = target_article.published_at.strftime('%d-%m-%Y') if hasattr(target_article, 'published_at') and target_article.published_at else "Tidak diketahui"
        m_date_str = article.published_at.strftime('%d-%m-%Y') if hasattr(article, 'published_at') and article.published_at else "Tidak diketahui"
        reasons.append(f"Rentang Waktu Terbit: Berita portofolio diterbitkan pada {t_date_str}, sedangkan rujukan resmi pada {m_date_str}.")

        # 7. Cek gambar dan gabungkan ke Final Score
        img_score = None
        is_identical = None
        if target_image_hash and article.image_url:
            compare_hash = _get_image_hash(article.image_url)
            if compare_hash:
                img_score = _calculate_image_similarity(target_image_hash, compare_hash)
                is_identical = img_score >= 95.0
                
                # Bobot Gambar: Jika ada gambar dan terdeteksi mirip (> 50%), berikan catatan. Jika > 85%, berikan penalti skor.
                if img_score is not None:
                    if img_score >= 85.0:
                        final_score = (final_score * 0.8) + (img_score * 0.2)
                        reasons.append(f"Peringatan Visual: Foto/gambar sampul terdeteksi IDENTIK atau sangat mirip dengan rujukan resmi (Skor Kemiripan Gambar: {round(img_score, 1)}%). Ini membuktikan sumber liputan yang sama dan menambah persentase plagiasi akhir.")
                    elif img_score >= 40.0:
                        reasons.append(f"Catatan Visual: Terdapat elemen kemiripan parsial pada foto sampul (Skor Kemiripan Gambar: {round(img_score, 1)}%).")
        elif target_article.image_url and article.image_url:
            reasons.append("Catatan Visual: Gambar sampul ditemukan, tetapi sistem gagal membandingkannya (akses gambar diblokir oleh server berita).")
        
        final_score = min(100.0, max(0.0, final_score))

        snippets_display = []
        for p in matched_pairs:
            quote_label = "[Kutipan Wajar] " if p.get("is_quote") else ""
            snippets_display.append(f"{quote_label}[Berita Anda: {p['target_sentence'][:120]}...] ↔ [Berita Resmi: {p['matched_sentence'][:120]}...] (Skor: {p['score']}%)")

        # Tampilkan hasil meskipun skor akhirnya rendah (misal < 15%), 
        # asalkan topik utamanya memang terdeteksi mirip (doc_score > 35).
        # Ini memberi tahu wartawan bahwa AI mendeteksi topik tersebut, 
        # namun memberikan apresiasi karena berhasil diparafrase.
        if final_score < 5.0 and doc_score < 35.0:
            continue

        final_results.append({
            "result": TextSimilarityResult(
                article_id=article.id,
                article_title=article.title,
                article_url=article.url,
                published_at=article.published_at,
                text_similarity_score=final_score,
                image_url=article.image_url,
                image_similarity_score=img_score,
                image_is_identical=is_identical,
                matching_snippets=snippets_display,
                reasons=reasons,
                matched_pairs=valid_matches,
            ),
            "doc_score": round(doc_score, 2),
            "sentence_score": round(sentence_score, 2),
            "time_penalty": time_penalty,
            "final_score": final_score,
            "reasons": reasons,
        })

    final_results.sort(key=lambda x: x["final_score"], reverse=True)
    top_final = final_results[:top_n]

    # 8. Simpan ke DB
    for item in top_final:
        res = item["result"]
        existing = db.query(SimilarityResult).filter(
            SimilarityResult.article_1_id == article_id,
            SimilarityResult.article_2_id == res.article_id
        ).first()
        if not existing:
            db_result = SimilarityResult(
                article_1_id=article_id,
                article_2_id=res.article_id,
                similarity_score=res.text_similarity_score,
                reasons=json.dumps(res.reasons) if res.reasons else None
            )
            db.add(db_result)
        else:
            existing.similarity_score = res.text_similarity_score
            existing.reasons = json.dumps(res.reasons) if res.reasons else None
            existing.analyzed_at = datetime.utcnow()

    db.commit()

    source_name = None
    if target_article.source:
        source_name = target_article.source.name

    return AnalyzeResponse(
        target_article_id=target_article.id,
        target_article_title=target_article.title,
        target_article_url=target_article.url,
        target_article_author=target_article.author,
        target_article_published_at=target_article.published_at,
        target_source_name=source_name,
        total_compared=len(official_articles),
        results=[item["result"] for item in top_final],
        _debug_details=[{
            "article_id": item["result"].article_id,
            "doc_score": item["doc_score"],
            "sentence_score": item["sentence_score"],
            "time_penalty": item["time_penalty"],
            "final_score": item["final_score"],
            "matched_pairs": item["result"].matched_pairs,
        } for item in top_final]
    )
