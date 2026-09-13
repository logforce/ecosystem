// SPDX-License-Identifier: Apache-2.0
use cogposix::{Client, TensorDesc};
use std::time::Duration;
#[cfg(target_os = "linux")]
mod bench;

fn main() {
    if let Err(error) = run() {
        eprintln!("cog: {error}");
        std::process::exit(1);
    }
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() == 1 && args[0] == "--help" {
        println!("cog --socket PATH demo [byte,byte,...]\ncog --socket PATH stats\ncog --socket PATH transport-bench (Linux)");
        return Ok(());
    }
    if args.len() < 3 || args[0] != "--socket" {
        return Err("use cog --help".into());
    }
    let mut client = Client::connect(&args[1])?;
    match args[2].as_str() {
        #[cfg(target_os = "linux")]
        "transport-bench" if args.len() == 3 => bench::run(&mut client)?,
        "stats" if args.len() == 3 => println!("{:?}", client.stats()?),
        "demo" if args.len() <= 4 => {
            let data = args
                .get(3)
                .map(String::as_str)
                .unwrap_or("0,1,254,255")
                .split(',')
                .map(str::parse::<u8>)
                .collect::<Result<Vec<_>, _>>()?;
            let model = client.model_open("mock.increment.v1")?;
            let input = client.buffer_alloc(data.len())?;
            let output = client.buffer_alloc(data.len())?;
            client.buffer_write(input, &data)?;
            let desc = TensorDesc {
                offset: 0,
                shape: vec![data.len() as u64],
            };
            let a = client.tensor_create(input, desc.clone())?;
            let b = client.tensor_create(output, desc)?;
            let job = client.submit(model, a, b, 0)?;
            client.wait(job, Duration::from_secs(5))?;
            println!(
                "model=mock.increment.v1 input={data:?} output={:?}",
                client.buffer_read(output)?
            );
            client.job_release(job)?;
            client.tensor_release(a)?;
            client.tensor_release(b)?;
            client.buffer_free(input)?;
            client.buffer_free(output)?;
            client.model_close(model)?;
            println!("cleanup={:?}", client.stats()?);
        }
        _ => return Err("use cog --help".into()),
    }
    Ok(())
}
