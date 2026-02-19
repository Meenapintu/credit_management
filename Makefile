.PHONY: clean build publishTest

clean:
	rm -rf build dist *.egg-info


build:
	python3 -m build


publish:
	python3 -m twine upload dist/*

publishTest:
	python3 -m twine upload --repository-url https://test.pypi.org/legacy/ dist/*



