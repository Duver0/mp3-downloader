const spotifyScraper = (function() {
    let songs = [];
    let lastProcessedMax = 0;
    let intervalId = null;
    let isRunning = false;
    let autoScrollEnabled = true;
    let scrollContainer = null;
    
    function findScrollContainer() {
        return document.querySelector('.main-view-container__scroll-node[data-overlayscrollbars="host"] > div');
    }
    
    function extract() {
        const currentSongs = [];
        const rows = document.querySelectorAll('[data-testid="tracklist-row"]');
        
        rows.forEach((row) => {
            try {
                const cells = row.querySelectorAll('[role="gridcell"]');
                let position = 0;
                let title = '';
                let artist = '';
                let album = '';
                
                cells.forEach(cell => {
                    const colIndex = parseInt(cell.getAttribute('aria-colindex'));
                    if (colIndex === 1) {
                        const span = cell.querySelector('span[data-encore-id="text"]');
                        position = parseInt(span?.innerText?.trim()) || 0;
                    } else if (colIndex === 2) {
                        const links = cell.querySelectorAll('a');
                        title = links[0]?.innerText?.trim() || '';
                        if (links.length > 1) {
                            artist = Array.from(links).slice(1).map(l => l.innerText?.trim()).filter(Boolean).join(', ');
                        }
                    } else if (colIndex === 3) {
                        album = cell.querySelector('a')?.innerText?.trim() || '';
                    }
                });
                
                if (title && artist && position > 0) {
                    currentSongs.push({ position, title, artist, album });
                }
            } catch (e) {}
        });
        
        return currentSongs;
    }
    
    let lastCount = 0;
    let noChangeTimer = null;
    const NO_CHANGE_THRESHOLD = 3000;
    
    function resetNoChangeTimer() {
        if (noChangeTimer) clearTimeout(noChangeTimer);
        noChangeTimer = setTimeout(() => {
            console.log('⏹️ Sin cambios en 3s - Deteniendo...');
            copyAndShow();
        }, NO_CHANGE_THRESHOLD);
    }
    
    function checkAndAdd() {
        const newSongs = extract();
        if (newSongs.length === 0) return;
        
        const currentMin = Math.min(...newSongs.map(s => s.position));
        
        if (lastProcessedMax > 0 && currentMin > lastProcessedMax + 1) {
            const missing = [];
            for (let i = lastProcessedMax + 1; i < currentMin; i++) missing.push(i);
            console.log('⚠️ FALTAN: ' + missing.join(', '));
            return;
        }
        
        let added = 0;
        newSongs.forEach(song => {
            if (!songs.find(s => s.position === song.position)) {
                songs.push(song);
                lastProcessedMax = Math.max(lastProcessedMax, song.position);
                added++;
            }
        });
        
        songs.sort((a, b) => a.position - b.position);
        if (added > 0) console.log('✓ ' + songs.length + ' canciones');
        
        if (songs.length !== lastCount) {
            lastCount = songs.length;
            resetNoChangeTimer();
        }
        
        if (autoScrollEnabled && songs.length < 873) {
            if (!scrollContainer) scrollContainer = findScrollContainer();
            if (scrollContainer) {
                scrollContainer.scrollTop = scrollContainer.scrollTop + 1000;
            }
        }
    }
    
    function start() {
        if (isRunning) return;
        isRunning = true;
        intervalId = setInterval(checkAndAdd, 500);
        console.log('🔄 Scraper iniciado (auto-scroll activo)');
        console.log('💡 Usa spotifyScraper.toggleAutoScroll() para activar/desactivar auto-scroll');
    }
    
    function stop() {
        if (intervalId) { clearInterval(intervalId); isRunning = false; }
        if (noChangeTimer) { clearTimeout(noChangeTimer); noChangeTimer = null; }
    }
    
    function reset() {
        stop();
        songs = [];
        lastProcessedMax = 0;
        lastCount = 0;
        console.log('🔄 Reseteado');
    }
    
    function toggleAutoScroll() {
        autoScrollEnabled = !autoScrollEnabled;
        console.log('Auto-scroll: ' + (autoScrollEnabled ? 'ON' : 'OFF'));
    }
    
    function copyAndShow() {
        const result = songs.map(s => s.title + ' - ' + s.artist + ' || ' + s.album).join('\n');
        
        console.log('\n=== RESULTADO ===\n');
        songs.forEach(s => console.log(s.title + ' - ' + s.artist + ' || ' + s.album));
        console.log('\nTotal: ' + songs.length);
        
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:99998';
        document.body.appendChild(overlay);
        
        const btn = document.createElement('button');
        btn.textContent = '📋 COPIAR ' + songs.length + ' CANCIONES';
        btn.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#1db954;color:#fff;padding:20px 40px;border-radius:30px;font-size:18px;cursor:pointer;z-index:99999;font-weight:bold;border:none;box-shadow:0 4px 20px rgba(0,0,0,0.5)';
        document.body.appendChild(btn);
        
        btn.onclick = () => {
            const ta = document.createElement('textarea');
            ta.value = result;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            document.body.removeChild(overlay);
            document.body.removeChild(btn);
            console.log('✅ ¡Copiado!');
        };
        
        return true;
    }
    
    start();
    
    return { start, stop, reset, copyAndShow, toggleAutoScroll };
})();