from jinja2 import Environment, FileSystemLoader
env = Environment(loader = FileSystemLoader(["."]))

base = "base.html"
sites = ["index_template.html", "diensten_template.html", "projecten_template.html", "veiligheid_template.html", "contact_template.html"]

projects_images_list = ['1','2','3','4','5','11','12','16','17','18']

for site in sites:
    template = env.get_template(site)
    #     rendered_template = template.render(base_template=base, projects_gallery=projects_gallery, products_gallery=products_gallery)
    rendered_template = template.render(base_template=base,projects_images_list=projects_images_list)
    
    with open(site.replace("_template.html", ".html"), "w") as fh:
        fh.write(rendered_template)
