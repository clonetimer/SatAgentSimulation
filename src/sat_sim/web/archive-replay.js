// Read a bounded window of the sealed journal. Scrubbing selects an original sample.
customElements.define('sat-archive-replay', class extends HTMLElement {
  setSession(session, api, catalog = session?.telemetry_catalog) {
    const stream = catalog?.find(item => item.stream_id === 'whole.orbit')
      || catalog?.find(item => item.fields?.length);
    const id = session?.archive_status === 'SEALED' && stream?.fields?.length ? session.session_id : null;
    if (this.sessionId === id) return;
    this.sessionId = id;
    this.replaceChildren();
    this.hidden = !id;
    if (!id) return;
    this.innerHTML = `<details><summary>回放已封存遥测</summary>
      <p data-source></p><label>起始时间（秒） <input type="number" min="0" step="any" value="0"></label>
      <button class="button button-secondary" type="button">读取时间窗</button>
      <p role="status"></p><label hidden>原始采样 <input type="range" min="0" step="1"></label>
      <pre></pre></details>`;
    this.querySelector('[data-source]').textContent = id + ' · ' + stream.stream_id;
    const start = this.querySelector('input[type=number]');
    const slider = this.querySelector('input[type=range]');
    const button = this.querySelector('button');
    const status = this.querySelector('[role=status]');
    const output = this.querySelector('pre');
    let frames = [];
    function showFrame() {
      const frame = frames[Number(slider.value)];
      output.textContent = frame ? [frame.sim_time_s + ' s · #' + frame.sequence + ' · ' + frame.quality,
        ...Object.entries(frame.values).map(([field, value]) => field + ' = ' + (value ?? '缺测'))].join('\n') : '';
    }
    slider.addEventListener('input', showFrame);
    button.addEventListener('click', async () => {
      const from = start.valueAsNumber;
      slider.parentElement.hidden = true;
      output.textContent = '';
      if (!Number.isFinite(from) || from < 0 || from > session.sim_time_s) {
        status.textContent = '请输入会话时间范围内的起始时间（0～' + session.sim_time_s + ' 秒）。';
        return;
      }
      const windowSeconds = Math.min(60, (stream.sample_s || 1) * 1000);
      const query = new URLSearchParams({ start_s: from, end_s: from + windowSeconds,
        fields: stream.fields.slice(0, 3).join(','), max_rows: 3000 });
      button.disabled = true;
      status.textContent = '正在核验归档…';
      try {
        const record = await api('/interactive/sessions/' + encodeURIComponent(id) + '/telemetry-history/'
          + encodeURIComponent(stream.stream_id) + '?' + query);
        if (record.session_id !== id || !record.exact_original_samples) throw new Error('归档来源不一致');
        frames = record.frames;
        status.textContent = frames.length + ' 条原始采样 · ' + (record.gaps.length ? '检测到序号缺口' : '序号连续');
        slider.max = String(Math.max(0, frames.length - 1));
        slider.value = '0';
        slider.parentElement.hidden = !frames.length;
        showFrame();
      } catch (error) {
        status.textContent = '读取失败：' + error.message;
      } finally {
        button.disabled = false;
      }
    });
  }
});
