// SPDX-License-Identifier: Apache-2.0
use cogposix::{Client, SharedBuffer, MAX_BUFFER};
use std::time::Instant;

pub fn run(client: &mut Client) -> Result<(), Box<dyn std::error::Error>> {
    let bytes = vec![37; MAX_BUFFER];
    let mut samples = [Vec::new(), Vec::new()];
    let mut traffic = [0u64; 2];
    for iteration in 0..18 {
        for mode in [iteration % 2, (iteration + 1) % 2] {
            let before = client.traffic();
            let start = Instant::now();
            if mode == 0 {
                let handle = client.buffer_alloc(bytes.len())?;
                client.buffer_write(handle, &bytes)?;
                let result = client.buffer_read(handle)?;
                client.buffer_free(handle)?;
                let elapsed = start.elapsed().as_micros();
                assert_eq!(result, bytes);
                if iteration >= 2 {
                    samples[mode].push(elapsed);
                }
            } else {
                let input = SharedBuffer::from_bytes(&bytes)?;
                let handle = client.buffer_import_file(input.file(), bytes.len())?;
                let (file, size) = client.buffer_export_file(handle)?;
                let result = SharedBuffer::from_file(file, size)?;
                client.buffer_free(handle)?;
                let elapsed = start.elapsed().as_micros();
                assert_eq!(result.bytes(), bytes);
                if iteration >= 2 {
                    samples[mode].push(elapsed);
                }
            }
            let after = client.traffic();
            if iteration >= 2 {
                traffic[mode] += after.0 - before.0 + after.1 - before.1;
            }
        }
    }
    for (mode, label) in ["inline", "sealed"].iter().enumerate() {
        samples[mode].sort_unstable();
        println!("transport={label} bytes={} samples=16 p50_us={} p95_us={} frame_bytes_per_roundtrip={}",
            bytes.len(), samples[mode][7], samples[mode][15], traffic[mode] / 16);
    }
    assert_eq!(client.stats()?.live_bytes, 0);
    println!("Includes allocation/import, population, read/export and handle release; excludes inference. Snapshot creation still copies data.");
    Ok(())
}
