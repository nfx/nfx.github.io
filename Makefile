.PHONY: site serve clean

site:
	python3 build.py --out site

serve: site
	python3 -m http.server -d site 8000

clean:
	rm -rf site
